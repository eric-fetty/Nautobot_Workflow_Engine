"""Publish the desired workflow files for one repository and retry when Git is ahead."""

import logging
import re
from datetime import timedelta
from pathlib import Path

from django.core.cache import cache
from django.utils import timezone
from git.exc import GitCommandError
from nautobot.core.utils.git import BranchDoesNotExist
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.secrets.exceptions import SecretError

from nautobot_workflow_engine.models import (
    GIT_STATUS_DISABLED_REPO,
    GIT_STATUS_IN_SYNC,
    GIT_STATUS_NOT_CONFIGURED,
    GIT_STATUS_NOT_SYNCED,
    GIT_STATUS_PENDING,
    MANAGED_BY_NAUTOBOT,
    WORKFLOW_PUBLISH_CONTENT,
    WORKFLOWS_CONTENT,
    Workflow,
    WorkflowRevision,
)
from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.validate import validate
from nautobot_workflow_engine.schema.yaml_io import to_yaml
from nautobot_workflow_engine.utilities.git import (
    NonFastForward,
    WorkflowGitRepo,
    acquire_repo_lock,
    actor,
    builder_url,
    file_sha256,
    plugin_setting,
    release_repo_lock,
    revision_url,
    sanitize_git_error,
    scrub_plain,
)
from nautobot_workflow_engine.utilities.sync_rules import apply_git_version, commit_touch, file_digest

logger = logging.getLogger(__name__)
UNPUSHED = (GIT_STATUS_PENDING, GIT_STATUS_NOT_SYNCED)


class PublishLog:
    """Collect job log lines without printing secrets."""

    def __init__(self, job_logger=None, job_result=None):
        """Keep the lines that were recorded."""
        self.job_logger = job_logger
        self.job_result = job_result
        self.lines = []

    def info(self, message, *args, **kwargs):
        """Record an informational line."""
        self._write("info", message % args if args else message)

    def warning(self, message, *args, **kwargs):
        """Record a warning."""
        self._write("warning", message % args if args else message)

    def error(self, message, *args, **kwargs):
        """Record an error."""
        self._write("error", message % args if args else message)

    def log(self, message="", **kwargs):
        """Record a line the sync helpers can pass as ``job_result.log``."""
        self._write("info", kwargs.get("message", message))

    def plain_error(self, message):
        """Record a message written for people. It has no Git output in it, so only URL userinfo is stripped."""
        self._write("error", message, plain=True)

    def _write(self, level, message, plain=False):
        text = scrub_plain(message) if plain else sanitize_git_error(message)
        self.lines.append(text)
        if self.job_logger is not None:
            getattr(self.job_logger, level, self.job_logger.info)(text)
        elif self.job_result is not None and hasattr(self.job_result, "log"):
            self.job_result.log(message=text)


PATH_CONFLICT = "wasn't created by this workflow"
PUBLISH_CONTENT_LABEL = "Workflow publishing (Nautobot → Git)"


def publish_blocker(repository):
    """Return why ``repository`` can't take a publish, in plain words, or "" when it can.

    The push uses the same credentials as Nautobot's own repository sync (and Golden Config):
    the repository's secrets group, access type HTTP(S), secret type Token, plus an optional
    Username. A group with only a token works.
    """
    if repository is None:
        return ""
    name = repository.name
    contents = set(repository.provided_contents or [])
    if WORKFLOW_PUBLISH_CONTENT not in contents or WORKFLOWS_CONTENT not in contents:
        return (
            f"Couldn't publish to {name}: the repository isn't set up for publishing. An admin needs to edit it "
            f"in Nautobot and tick both “workflows” and “{PUBLISH_CONTENT_LABEL}” under Provides."
        )
    group = getattr(repository, "secrets_group", None)
    if group is None:
        return (
            f"Couldn't push to {name}: the repository has no token. An admin needs to give it a secrets group "
            "with an HTTP(S) Token secret."
        )
    associations = getattr(group, "secrets_group_associations", None)
    has_token = (
        associations is not None
        and associations.filter(
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN,
        ).exists()
    )
    if not has_token:
        return (
            f"Couldn't push to {name}: its secrets group “{group.name}” has no token. Add a secret with access "
            "type HTTP(S) and secret type Token."
        )
    remote = str(getattr(repository, "remote_url", "") or "").lower()
    if remote and not remote.startswith(("https://", "http://")):
        return f"Couldn't push to {name}: publishing needs an https:// repository address."
    return ""


def repo_can_publish(repository):
    """A repo publishes only when it opts in and has an HTTP(S) token."""
    return repository is not None and not publish_blocker(repository)


def known_secrets(repository):
    """Secret values to strip from errors. Failures here are ignored."""
    group = getattr(repository, "secrets_group", None)
    if group is None:
        return []
    found = []
    pairs = (
        (SecretsGroupAccessTypeChoices.TYPE_HTTP, SecretsGroupSecretTypeChoices.TYPE_TOKEN),
        (SecretsGroupAccessTypeChoices.TYPE_HTTP, SecretsGroupSecretTypeChoices.TYPE_USERNAME),
        (SecretsGroupAccessTypeChoices.TYPE_GENERIC, SecretsGroupSecretTypeChoices.TYPE_KEY),
    )
    for access, kind in pairs:
        try:
            value = group.get_secret_value(access, kind, obj=repository)
        except Exception:
            logger.debug("Skipping a secret that could not be read.", exc_info=True)
            continue
        if value:
            found.append(str(value))
    return found


def default_git_path(workflow):
    """Return the stored path, or ``<workflow_dir>/<key>.yaml``."""
    if getattr(workflow, "git_path", ""):
        return workflow.git_path
    directory = str(plugin_setting("workflow_dir", "workflows") or "workflows").strip("/")
    return f"{directory}/{workflow.key}.yaml"


def render_file(workflow, definition):
    """Canonical YAML plus the managed-by header."""
    header = (
        f"# Managed by Nautobot Workflow Engine: {builder_url(workflow)}\n"
        "# Edits here are imported into Nautobot on the next repository sync (latest change wins).\n"
    )
    body = to_yaml(definition or {})
    return (header + body).encode("utf-8")


def schedule_publish(workflow, user):
    """Mark a saved workflow pending and enqueue a push. A failed enqueue does not undo the save."""
    if workflow.source_repo_id is None:
        if workflow.git_status != GIT_STATUS_NOT_CONFIGURED:
            workflow.git_status = GIT_STATUS_NOT_CONFIGURED
            workflow.save(update_fields=["git_status", "last_updated"])
        return None
    if not getattr(workflow, "git_path", ""):
        workflow.git_path = default_git_path(workflow)
    blocker = publish_blocker(workflow.source_repo)
    if blocker:
        workflow.git_status = GIT_STATUS_DISABLED_REPO
        workflow.git_error = blocker
        workflow.save(update_fields=["git_status", "git_error", "git_path", "last_updated"])
        return None
    workflow.git_status = GIT_STATUS_PENDING
    workflow.git_error = ""
    workflow.save(update_fields=["git_status", "git_error", "git_path", "last_updated"])
    try:
        return enqueue_publish(workflow.source_repo, user)
    except Exception as exc:
        logger.error("Could not start the publish job for %s: %s", workflow.source_repo.name, sanitize_git_error(exc))
        message = (
            f"Couldn't start publishing to {workflow.source_repo.name}. It tries again shortly. "
            "If it keeps failing, check that the Nautobot worker is running."
        )
        workflow.git_status = GIT_STATUS_NOT_SYNCED
        workflow.git_error = message
        workflow.git_retry_count = (workflow.git_retry_count or 0) + 1
        workflow.git_next_retry_at = next_retry_at(workflow.git_retry_count)
        workflow.save(update_fields=["git_status", "git_error", "git_retry_count", "git_next_retry_at", "last_updated"])
        logger.info("Publish enqueue failed: %s", message)
        return None


def enqueue_publish(repository, user):
    """Enqueue at most one publish job per repository. Return the job result, or None when one is queued."""
    queued = f"nwe:git:queued:{repository.pk}"
    if not cache.add(queued, "1", timeout=int(plugin_setting("git_push_lock_timeout", 300))):
        return None
    try:
        from nautobot.extras.models import Job as JobModel
        from nautobot.extras.models.jobs import JobResult

        job_model = JobModel.objects.get(
            module_name="nautobot_workflow_engine.jobs",
            job_class_name="PublishWorkflowsToGit",
        )
        if user is None or not getattr(user, "is_authenticated", False):
            raise RuntimeError("A publish job needs the user who saved.")
        return JobResult.enqueue_job(job_model, user, repository_id=str(repository.pk))
    except Exception:
        cache.delete(queued)
        raise


def enqueue_repository_sync(repository, user):
    """Enqueue core GitRepositorySync, at most once per 15 seconds for this repo."""
    marker = f"nwe:git:sync:{repository.pk}"
    if not cache.add(marker, "1", timeout=15):
        return None
    from nautobot.extras.datasources.git import enqueue_pull_git_repository_and_refresh_data

    actor_user = user if user is not None and getattr(user, "is_authenticated", False) else _fallback_user()
    if actor_user is None:
        return None
    return enqueue_pull_git_repository_and_refresh_data(repository, actor_user)


def refresh_on_open(workflow, user):
    """Check the remote and, when it is ahead, sync in-process so the editor loads that version."""
    repository = workflow.source_repo
    if repository is None:
        return {"state": "current", "job_result": None, "reason": "", "updated": False}
    before = (workflow.git_commit, workflow.git_content_hash, workflow.name, str(workflow.last_updated))
    from nautobot_workflow_engine.utilities.git_probe import GitProbeError, GitRemoteProbe

    try:
        head = GitRemoteProbe(repository).remote_head()
    except Exception as exc:
        reason = sanitize_git_error(exc, known_secrets(repository))
        if isinstance(exc, GitProbeError):
            reason = sanitize_git_error(exc, known_secrets(repository))
        return {"state": "unverified", "job_result": None, "reason": reason, "updated": False}
    if head == (repository.current_head or ""):
        return {"state": "current", "job_result": None, "reason": "", "updated": False}
    log = PublishLog()
    try:
        prepare_working_copy(repository, log)
        from nautobot_workflow_engine.datasources import sync_workflows_from_repo

        sync_workflows_from_repo(repository, log, delete=False)
    except Exception as exc:
        return {
            "state": "unverified",
            "job_result": None,
            "reason": sanitize_git_error(exc, known_secrets(repository)),
            "updated": False,
        }
    job_result = None
    try:
        job_result = enqueue_repository_sync(repository, user)
    except Exception as exc:
        log.warning("Could not enqueue repository sync: %s", sanitize_git_error(exc, known_secrets(repository)))
    workflow.refresh_from_db()
    after = (workflow.git_commit, workflow.git_content_hash, workflow.name, str(workflow.last_updated))
    return {
        "state": "syncing" if job_result is not None else "current",
        "job_result": str(getattr(job_result, "pk", "") or "") or None,
        "reason": "",
        "updated": before != after,
    }


def publish_repository(repository_id, user, job_logger=None, job_result=None, lock_timeout=None):
    """Write the desired files, commit as ``user``, and push. Failures stay on the workflow row."""
    from nautobot.extras.models import GitRepository

    repository = GitRepository.objects.get(pk=repository_id)
    log = PublishLog(job_logger, job_result)
    timeout = int(plugin_setting("git_push_lock_timeout", 300) if lock_timeout is None else lock_timeout)
    lock = acquire_repo_lock(repository.pk, timeout)
    if lock is None:
        _fail(
            repository,
            f"Couldn't push to {repository.name}: another publish to it is still running. It tries again shortly.",
            job_result,
            log,
        )
        return False
    cache.delete(f"nwe:git:queued:{repository.pk}")
    try:
        return _publish_locked(repository, user, log, job_result)
    except SecretError as exc:
        log_full_error(repository, exc, log)
        _fail(repository, readable_failure(exc, repository), job_result, log)
        raise
    except Exception as exc:
        log_full_error(repository, exc, log)
        _fail(repository, readable_failure(exc, repository), job_result, log)
        return False
    finally:
        release_repo_lock(lock)


def run_git_maintenance(log=None):
    """Enqueue a sync when a publish repo moved, and retry pushes that are due."""
    from nautobot.extras.models import GitRepository

    recorder = log or PublishLog()
    from nautobot_workflow_engine.utilities.git_probe import GitRemoteProbe

    for repository in GitRepository.objects.all():
        contents = set(repository.provided_contents or [])
        if WORKFLOW_PUBLISH_CONTENT not in contents:
            continue
        try:
            head = GitRemoteProbe(repository).remote_head()
        except Exception as exc:
            recorder.warning(
                "Could not check %s: %s",
                repository.name,
                sanitize_git_error(exc, known_secrets(repository)),
            )
            head = None
        if head and head != (repository.current_head or ""):
            try:
                enqueue_repository_sync(repository, _fallback_user())
            except Exception as exc:
                recorder.warning("Could not enqueue sync: %s", sanitize_git_error(exc))
        due = Workflow.objects.filter(
            source_repo=repository,
            git_status=GIT_STATUS_NOT_SYNCED,
            git_next_retry_at__isnull=False,
            git_next_retry_at__lte=timezone.now(),
        )
        if due.exists():
            try:
                enqueue_publish(repository, _author_user(due.order_by("-last_updated").first()))
            except Exception:
                publish_repository(repository.pk, _author_user(due.first()), job_logger=recorder)


def log_full_error(repository, exc, log):
    """Keep the whole Git error, with secrets removed, in the server log and the job log."""
    full = sanitize_git_error(exc, known_secrets(repository))
    logger.error("Publishing workflows to %s failed: %s", getattr(repository, "name", "?"), full)
    log.error(f"Full error: {full}")


_PROTECTED = ("protected branch", "protected_branch", "gh006", "protected", "hook declined", "pre-receive")
_NOT_FOUND = ("repository not found", "not found", "404", "does not appear to be a git repository")
_NO_WRITE = ("write access", "permission to", "403", "not allowed to push", "permission denied")
_AUTH = (
    "authentication failed",
    "401",
    "could not read username",
    "could not read password",
    "invalid username or password",
    "terminal prompts disabled",
    "invalid credentials",
    "bad credentials",
)
_UNREACHABLE = (
    "could not resolve host",
    "connection refused",
    "connection timed out",
    "timed out",
    "failed to connect",
    "network is unreachable",
    "ssl certificate problem",
)


def _git_said(text):
    """The most useful line of a Git error: stderr's last fatal/error/remote line."""
    match = re.search(r"stderr:\s*'(.*)'", text, re.S)
    body = match.group(1) if match else text
    lines = [line.strip() for line in body.replace("\\n", "\n").splitlines() if line.strip()]
    for prefix in ("fatal:", "error:", "remote:", "!"):
        hits = [line for line in lines if line.lower().startswith(prefix)]
        if hits:
            return hits[-1]
    return lines[-1] if lines else ""


def readable_failure(exc, repository):
    """Say why the push failed in plain words. Never includes the token."""
    secrets = known_secrets(repository) if repository is not None else []
    text = sanitize_git_error(exc, secrets)
    lowered = text.lower()
    name = getattr(repository, "name", "") or "the repository"
    branch = getattr(repository, "branch", "") or "main"
    start = f"Couldn't push to {name}: "
    if PATH_CONFLICT in text:
        return text
    if isinstance(exc, SecretError):
        return (
            start + "the token in its secrets group couldn't be read. Check the secret's settings, for example that "
            "its environment variable is set on the Nautobot server and worker."
        )
    if isinstance(exc, NonFastForward) or "non-fast-forward retries ran out" in lowered:
        return start + f"others kept pushing to “{branch}” while this was publishing. It tries again shortly."
    if isinstance(exc, BranchDoesNotExist) or "couldn't find remote ref" in lowered or "doesn't exist" in lowered:
        return start + f"the branch “{branch}” doesn't exist there. Create it, or change the repository's branch."
    if any(marker in lowered for marker in _PROTECTED):
        return (
            start + f"the branch “{branch}” is protected. Allow the token's account to push to it, or point the "
            "repository at a branch it can push to."
        )
    if any(marker in lowered for marker in _AUTH):
        return (
            start + "authentication failed. Check that the token in its secrets group is valid and hasn't expired. "
            "If your Git server also needs a username, add a Username secret to the same group."
        )
    if any(marker in lowered for marker in _NO_WRITE):
        return start + "the token can read the repository but can't push to it. Give it write access."
    if (
        "repository not found" in lowered
        or "does not appear to be a git repository" in lowered
        or ("not found" in lowered and "remote ref" not in lowered)
    ):
        return start + "the repository wasn't found at its address. Check the address, and that the token can see it."
    if any(marker in lowered for marker in _UNREACHABLE):
        return start + "couldn't reach the Git server. Check the address and the network from the Nautobot worker."
    said = _git_said(text)
    if said:
        return (start + f"Git said “{said}”.")[:500]
    return f"Couldn't push to {name}. The server log has the full error."


def next_retry_at(retry_count):
    """Backoff from ``git_push_retry_backoff_minutes``, then stay on the last value."""
    schedule = list(plugin_setting("git_push_retry_backoff_minutes", [1, 5, 15, 60]) or [60])
    index = min(max(retry_count, 1), len(schedule)) - 1
    minutes = schedule[index]
    return timezone.now() + timedelta(minutes=int(minutes))


def prepare_working_copy(repository, log):
    """Fetch the remote tip, or create the local branch when the remote is still empty."""
    from nautobot.extras.datasources.git import (
        ensure_git_repository,
        get_repo_from_url_to_path_and_from_branch,
    )

    try:
        ensure_git_repository(repository, logger=log)
    except BranchDoesNotExist:
        _init_empty_branch(repository, log)
    except Exception as exc:
        if _missing_branch(exc):
            _init_empty_branch(repository, log)
        else:
            raise
    info = get_repo_from_url_to_path_and_from_branch(repository)
    helper = WorkflowGitRepo(
        str(info.to_path),
        info.from_url,
        clone_initially=False,
        branch=info.from_branch,
        nautobot_repo_obj=repository,
    )
    helper.branch_name = info.from_branch or repository.branch or "main"
    return helper


def _publish_locked(repository, user, log, job_result):
    helper = prepare_working_copy(repository, log)

    def regenerate():
        from git import Repo

        helper.repo = Repo(helper.repo.working_tree_dir)
        message, author = _write_desired(helper, repository, user, log, job_result)
        if message is None:
            log.info("no changes")
            return
        committed = helper.commit_with_added(message, author_actor(author or user), committer_actor())
        if not committed:
            log.info("no changes")

    message, author = _write_desired(helper, repository, user, log, job_result)
    if message is None:
        log.info("no changes")
        _mark_clean(repository, helper, job_result)
        return True
    committed = helper.commit_with_added(message, author_actor(author or user), committer_actor())
    if not committed:
        log.info("no changes")
        _mark_clean(repository, helper, job_result)
        return True
    helper.push_derived(regenerate, max_retries=3)
    _mark_clean(repository, helper, job_result)
    repository.current_head = helper.head
    repository.save(update_fields=["current_head"])
    log.info("Pushed %s", helper.head)
    return True


def _write_desired(helper, repository, fallback_user, log, job_result):
    """Apply the write rule. Return (message, author) or (None, None) when nothing should be committed."""
    root = Path(helper.repo.working_tree_dir)
    branch = helper.branch_name
    workflows = list(
        Workflow.objects.filter(source_repo=repository, managed_by=MANAGED_BY_NAUTOBOT, git_status__in=UNPUSHED)
    )
    archived = list(
        Workflow.objects.filter(source_repo=repository, managed_by=MANAGED_BY_NAUTOBOT, archived=True).exclude(
            git_status=GIT_STATUS_IN_SYNC
        )
    )
    seen = {item.pk for item in workflows}
    for item in archived:
        if item.pk not in seen:
            workflows.append(item)
    entries = []
    overwrote = []
    for workflow in workflows:
        if not workflow.git_path:
            workflow.git_path = default_git_path(workflow)
        relative = workflow.git_path
        path = root / relative
        action = _write_rule(helper, repository, workflow, path, relative, log, job_result)
        if action is None:
            continue
        entries.append(action)
        if action.get("overwrote"):
            overwrote.append(action["overwrote"])
    if not entries:
        return None, None
    entries.sort(key=lambda item: item["published_at"] or timezone.now(), reverse=True)
    author = entries[0]["user"] or fallback_user
    message = _commit_message(entries, job_result, overwrote)
    _ = branch
    return message, author


def _write_rule(helper, repository, workflow, path, relative, log, job_result):
    revision = workflow.published_revision if workflow.published_revision_id else None
    definition = revision.definition if revision is not None else (workflow.raw_definition or {})
    desired = b"" if workflow.archived else render_file(workflow, definition)
    desired_hash = file_sha256(desired) if desired else ""
    exists = path.is_file()
    current = path.read_bytes() if exists else None
    current_hash = file_sha256(current) if current is not None else ""
    known = workflow.git_content_hash or ""
    pushed_before = bool(workflow.git_commit or known)
    touch = commit_touch(helper.repo.working_tree_dir, relative)

    if workflow.archived:
        return _archive_file(workflow, path, exists, pushed_before, touch, revision, log)

    if exists and not pushed_before and current_hash != desired_hash:
        workflow.git_status = GIT_STATUS_NOT_SYNCED
        workflow.git_error = (
            f"Couldn't push to {repository.name}: {relative} already exists there and {PATH_CONFLICT}. "
            "Remove or rename that file in Git, or give this workflow a different ID."
        )
        workflow.git_retry_count = (workflow.git_retry_count or 0) + 1
        workflow.git_next_retry_at = next_retry_at(workflow.git_retry_count)
        workflow.git_path = relative
        workflow.save(
            update_fields=[
                "git_status",
                "git_error",
                "git_retry_count",
                "git_next_retry_at",
                "git_path",
                "last_updated",
            ]
        )
        log.error(workflow.git_error)
        return None

    if current_hash == desired_hash:
        return None

    if not exists and not pushed_before:
        _write_bytes(path, desired)
        return _entry(workflow, revision, desired)
    if known and current_hash == known:
        _write_bytes(path, desired)
        return _entry(workflow, revision, desired)
    if not exists and pushed_before:
        return _deleted_upstream(workflow, path, desired, touch, revision, log, job_result, repository, relative)
    if current_hash != known:
        return _diverged(
            helper, repository, workflow, path, desired, current, touch, revision, relative, log, job_result
        )
    _write_bytes(path, desired)
    return _entry(workflow, revision, desired)


def _archive_file(workflow, path, exists, pushed_before, touch, revision, log):
    published_at = revision.published_at if revision is not None else None
    if not exists:
        return None
    if pushed_before and touch["at"] is not None and published_at is not None and touch["at"] > published_at:
        log.info("E-NWE-207 Newer Git change kept for archived workflow %s.", workflow.key)
        return None
    path.unlink()
    log.info("Removed %s", workflow.git_path)
    return _entry(workflow, revision, b"", verb="archive")


def _deleted_upstream(workflow, path, desired, touch, revision, log, job_result, repository, relative):
    published_at = revision.published_at if revision is not None else None
    if touch["at"] is not None and published_at is not None and touch["at"] > published_at:
        workflow.enabled = False
        workflow.archived = True
        workflow.git_status = GIT_STATUS_IN_SYNC
        workflow.git_commit = touch["sha"] or workflow.git_commit
        workflow.git_error = ""
        workflow.git_retry_count = 0
        workflow.git_next_retry_at = None
        workflow.save()
        log.info(
            "E-NWE-207 Archived %s because Git deleted it at %s.",
            workflow.key,
            touch["sha"],
        )
        return None
    _write_bytes(path, desired)
    log.info("Recreated %s from the newer database save.", relative)
    return _entry(workflow, revision, desired)


def _diverged(helper, repository, workflow, path, desired, current, touch, revision, relative, log, job_result):
    published_at = revision.published_at if revision is not None else None
    git_newer = touch["at"] is not None and published_at is not None and touch["at"] > published_at
    if git_newer:
        parsed = _load_yaml(current)
        errors = validate(normalize(parsed), repo=repository, enforce_policy=False, check_scripts=False)
        if errors or not isinstance(parsed, dict):
            workflow.git_status = GIT_STATUS_NOT_SYNCED
            workflow.git_error = (
                f"Couldn't push to {repository.name}: someone changed {relative} in Git after this was published, "
                "and their version isn't valid. Nautobot kept the published version. Fix or revert the file in Git."
            )
            workflow.save(update_fields=["git_status", "git_error", "last_updated"])
            log.error(workflow.git_error)
            return None
        reason = f"newer Git change {touch['sha']} by {touch['author']}"
        apply_git_version(repository, workflow, parsed, current, relative, log, reason)
        log.info("E-NWE-207 Applied newer Git change %s and superseded the unpushed save.", touch["sha"])
        return None
    _write_bytes(path, desired)
    log.info("E-NWE-206 Overwrote older Git commit %s.", touch["sha"] or "?")
    entry = _entry(workflow, revision, desired)
    entry["overwrote"] = touch["sha"]
    if revision is not None and touch["sha"]:
        revision.superseded_reason = f"Overwrote Git commit {touch['sha']}."
        revision.save(update_fields=["superseded_reason"])
    return entry


def _entry(workflow, revision, payload, verb=None):
    if verb is None:
        if workflow.archived:
            verb = "archive"
        elif revision is not None and revision.source == WorkflowRevision.SOURCE_GIT_IMPORT:
            verb = "adopt"
        else:
            verb = "publish"
    summary = ""
    user = None
    published_at = None
    if revision is not None:
        summary = revision.comment or ""
        user = revision.published_by or revision.created_by
        published_at = revision.published_at
    return {
        "workflow": workflow,
        "revision": revision,
        "verb": verb,
        "summary": summary,
        "user": user,
        "published_at": published_at,
        "payload": payload,
        "overwrote": "",
    }


def _commit_message(entries, job_result, overwrote):
    job_id = str(getattr(job_result, "pk", "") or "")
    if len(entries) == 1:
        subject = _subject(entries[0])
        body = _trailers(entries[0], job_id)
    else:
        subject = f"workflows: publish {len(entries)} workflow(s)"[:72]
        lines = [
            f"workflow({item['workflow'].key}): {item['verb']} r{item['revision'].number if item['revision'] else 0}"
            for item in entries
        ]
        body = "\n".join(lines) + "\n\n" + "\n".join(_trailers(item, job_id) for item in entries)
    coauthors = []
    primary = entries[0]["user"]
    seen = set()
    for item in entries:
        person = item["user"]
        if person is None or person == primary or person.pk in seen:
            continue
        seen.add(person.pk)
        coauthors.append(f"Co-authored-by: {_person_name(person)} <{_person_email(person)}>")
    trailer = body
    if any(item.get("overwrote") for item in entries):
        shas = [item["overwrote"] for item in entries if item.get("overwrote")]
        trailer += "\n" + "\n".join(f"Nautobot-Overwrote: {sha}" for sha in shas)
    if coauthors:
        trailer += "\n" + "\n".join(coauthors)
    _ = overwrote
    return f"{subject}\n\n{trailer}\n"


def _subject(item):
    number = item["revision"].number if item["revision"] is not None else 0
    base = f"workflow({item['workflow'].key}): {item['verb']} r{number}"
    summary = (item.get("summary") or "").strip()
    if summary and not summary.lower().startswith("duplicated") and summary.lower() != "imported from git.":
        extra = f": {summary}"
        room = 72 - len(base)
        if room > 2:
            base = (base + extra)[:72]
    return base[:72]


def _trailers(item, job_id):
    workflow = item["workflow"]
    revision = item["revision"]
    number = revision.number if revision is not None else 0
    username = getattr(item["user"], "username", "") or ""
    lines = [
        f"Nautobot-Workflow: {workflow.key}",
        f"Nautobot-Revision: {revision.pk if revision is not None else ''}",
        f"Nautobot-User: {username}",
        f"Nautobot-Url: {revision_url(workflow, number) if number else builder_url(workflow)}",
        f"Nautobot-Job-Result: {job_id}",
    ]
    return "\n".join(lines)


def _mark_clean(repository, helper, job_result):
    head = ""
    try:
        head = helper.head
    except Exception:
        head = repository.current_head or ""
    root = Path(helper.repo.working_tree_dir)
    rows = Workflow.objects.filter(source_repo=repository, managed_by=MANAGED_BY_NAUTOBOT, git_status__in=UNPUSHED)
    for workflow in rows:
        relative = workflow.git_path or default_git_path(workflow)
        path = root / relative
        if PATH_CONFLICT in (workflow.git_error or ""):
            continue
        workflow.git_status = GIT_STATUS_IN_SYNC
        workflow.git_commit = head
        workflow.git_error = ""
        workflow.git_retry_count = 0
        workflow.git_next_retry_at = None
        if getattr(getattr(job_result, "_meta", None), "model_name", "") == "jobresult":
            workflow.git_job_result = job_result
        if path.is_file():
            workflow.git_content_hash = file_digest(path.read_bytes())
        elif workflow.archived:
            workflow.git_content_hash = ""
        workflow.git_path = relative
        workflow.save()
        revision = workflow.published_revision if workflow.published_revision_id else None
        if revision is not None:
            revision.git_commit = head
            revision.save(update_fields=["git_commit"])


def _fail(repository, message, job_result, log):
    # ``message`` is written for people (see ``readable_failure``). Core's sanitizer would redact
    # words such as "secrets group", so only secret values and URL userinfo are stripped here.
    safe = scrub_plain(message, known_secrets(repository))
    log.plain_error(safe)
    rows = Workflow.objects.filter(source_repo=repository, managed_by=MANAGED_BY_NAUTOBOT, git_status__in=UNPUSHED)
    for workflow in rows:
        if PATH_CONFLICT in (workflow.git_error or ""):
            continue
        workflow.git_status = GIT_STATUS_NOT_SYNCED
        workflow.git_error = safe
        workflow.git_retry_count = (workflow.git_retry_count or 0) + 1
        workflow.git_next_retry_at = next_retry_at(workflow.git_retry_count)
        if getattr(getattr(job_result, "_meta", None), "model_name", "") == "jobresult":
            workflow.git_job_result = job_result
        workflow.save()


def _write_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _load_yaml(payload):
    import yaml

    loaded = yaml.safe_load(payload.decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _init_empty_branch(repository, log):
    from nautobot.core.utils.git import GIT_ENVIRONMENT
    from nautobot.extras.datasources.git import get_repo_from_url_to_path_and_from_branch

    info = get_repo_from_url_to_path_and_from_branch(repository)
    path = Path(str(info.to_path))
    path.mkdir(parents=True, exist_ok=True)
    branch = info.from_branch or "main"
    helper = WorkflowGitRepo(
        str(path), info.from_url, clone_initially=False, branch=branch, nautobot_repo_obj=repository
    )
    try:
        with helper.repo.git.custom_environment(**GIT_ENVIRONMENT):
            helper.repo.remotes.origin.fetch()
    except GitCommandError:
        log.info("Remote has no commits yet. The first push will create %s.", branch)
    try:
        helper.repo.git.checkout("-B", branch)
    except GitCommandError:
        helper.repo.git.symbolic_ref("HEAD", f"refs/heads/{branch}")
    helper.branch_name = branch


def _missing_branch(exc):
    text = str(exc).lower()
    markers = (
        "remote branch",
        "not found in upstream",
        "couldn't find remote ref",
        "could not find remote ref",
        "does not exist",
        "empty",
    )
    if any(marker in text for marker in markers):
        return "authentication" not in text and "401" not in text and "403" not in text
    return False


def author_actor(user):
    """Git author for the saving user. Retries pass that same user."""
    if user is None:
        return committer_actor()
    return actor(_person_name(user), _person_email(user))


def committer_actor():
    """Git committer from plugin settings."""
    return actor(
        plugin_setting("git_committer_name", "Nautobot Workflow Engine"),
        plugin_setting("git_committer_email", "nautobot-workflow-engine@localhost"),
    )


def _person_name(user):
    full = ""
    if hasattr(user, "get_full_name"):
        full = (user.get_full_name() or "").strip()
    return full or getattr(user, "username", "") or "Nautobot"


def _person_email(user):
    email = (getattr(user, "email", "") or "").strip()
    if email:
        return email
    domain = plugin_setting("git_author_email_domain", "users.noreply.nautobot.local")
    return f"{getattr(user, 'username', 'user')}@{domain}"


def _author_user(workflow):
    revision = workflow.published_revision if getattr(workflow, "published_revision_id", None) else None
    if revision is not None and revision.published_by_id:
        return revision.published_by
    return _fallback_user()


def _fallback_user():
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return user_model.objects.filter(is_active=True).order_by("-is_superuser").first()
