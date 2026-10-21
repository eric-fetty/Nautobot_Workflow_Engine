"""Apply a Git file to a Nautobot-managed workflow when Git is the later change."""

import hashlib
from pathlib import Path

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from git import Repo
from git.exc import GitCommandError

from nautobot_workflow_engine.models import (
    GIT_STATUS_DISABLED_REPO,
    GIT_STATUS_GIT_INVALID,
    GIT_STATUS_IN_SYNC,
    GIT_STATUS_NOT_CONFIGURED,
    GIT_STATUS_NOT_SYNCED,
    GIT_STATUS_PENDING,
    MANAGED_BY_NAUTOBOT,
    WorkflowRevision,
)
from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.validate import validate
from nautobot_workflow_engine.schema.yaml_io import content_hash, to_yaml

UNPUSHED = {GIT_STATUS_PENDING, GIT_STATUS_NOT_SYNCED}


def file_digest(payload):
    """SHA-256 of the on-disk file, including the managed-by header."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def commit_touch(repo_path, relative_path):
    """Return sha, author, and committer time of the last commit that touched ``relative_path``."""
    try:
        repo = Repo(repo_path)
        text = repo.git.log("-1", "--format=%H%x00%an%x00%cI", "--", relative_path)
    except (GitCommandError, Exception):
        return {"sha": "", "author": "", "at": None}
    if not str(text).strip():
        return {"sha": "", "author": "", "at": None}
    sha, author, raw_when = (str(text).split("\x00") + ["", "", ""])[:3]
    moment = parse_datetime(raw_when)
    if moment is not None and timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.utc)
    return {"sha": sha, "author": author, "at": moment}


def consider_nautobot_file(repository, workflow, raw_bytes, parsed, job_result, relative_path):
    """Apply latest-wins for one file that belongs to a same-repo Nautobot workflow.

    Return True when the caller must not also import the file as a Git-managed workflow.
    """
    if getattr(workflow, "managed_by", None) != MANAGED_BY_NAUTOBOT:
        return False
    digest = file_digest(raw_bytes)
    status = getattr(workflow, "git_status", GIT_STATUS_NOT_CONFIGURED) or GIT_STATUS_NOT_CONFIGURED
    known = getattr(workflow, "git_content_hash", "") or ""
    if status in {GIT_STATUS_NOT_CONFIGURED, GIT_STATUS_DISABLED_REPO} and not known:
        job_result.log(
            message=(
                f"Leaving Nautobot-managed workflow '{workflow.key}' unchanged. " "It has not been published to Git."
            )
        )
        return True
    if known and digest == known:
        return True
    errors = _errors(parsed, repository)
    touch = commit_touch(getattr(repository, "filesystem_path", ""), relative_path)
    if errors:
        _mark_invalid(workflow, errors, touch, job_result)
        return True
    if status in UNPUSHED:
        published_at = _published_at(workflow)
        if touch["at"] is not None and published_at is not None and touch["at"] > published_at:
            _apply(repository, workflow, parsed, raw_bytes, touch, relative_path, job_result, supersede=True)
            return True
        job_result.log(
            message=(
                f"Keeping the unpushed Nautobot save for '{workflow.key}'. "
                "The pending push overwrites the older Git file."
            )
        )
        return True
    _apply(repository, workflow, parsed, raw_bytes, touch, relative_path, job_result, supersede=False)
    return True


def apply_git_version(repository, workflow, parsed, raw_bytes, relative_path, job_result, reason):
    """Replace the live definition with the Git file and supersede the unpushed revision."""
    touch = commit_touch(getattr(repository, "filesystem_path", ""), relative_path)
    _apply(repository, workflow, parsed, raw_bytes, touch, relative_path, job_result, supersede=True, reason=reason)
    return touch


def archive_if_deleted(repository, synced_keys, job_result):
    """Archive in-sync workflows whose file disappeared, when Git is the later change."""
    from nautobot_workflow_engine.models import Workflow

    lookup = getattr(getattr(Workflow, "objects", None), "filter", None)
    if not callable(lookup):
        return
    try:
        rows = list(
            lookup(source_repo=repository, managed_by=MANAGED_BY_NAUTOBOT, archived=False).exclude(key__in=synced_keys)
        )
    except Exception:
        return
    repo_path = getattr(repository, "filesystem_path", "")
    for workflow in rows:
        status = workflow.git_status or GIT_STATUS_NOT_CONFIGURED
        if status in {GIT_STATUS_NOT_CONFIGURED, GIT_STATUS_DISABLED_REPO} and not workflow.git_commit:
            continue
        relative = workflow.git_path or ""
        touch = commit_touch(repo_path, relative) if relative else {"sha": "", "author": "", "at": None}
        published_at = _published_at(workflow)
        if status in UNPUSHED and published_at is not None and (touch["at"] is None or published_at >= touch["at"]):
            continue
        if status not in UNPUSHED and status != GIT_STATUS_IN_SYNC and not workflow.git_commit:
            continue
        author = touch["author"] or "someone"
        when = touch["at"].isoformat() if touch["at"] else ""
        note = f"Deleted in Git by {author} at {when}".strip()
        workflow.enabled = False
        workflow.archived = True
        workflow.git_status = GIT_STATUS_IN_SYNC
        workflow.git_error = ""
        if touch["sha"]:
            workflow.git_commit = touch["sha"]
        workflow.save(update_fields=["enabled", "archived", "git_status", "git_error", "git_commit", "last_updated"])
        _note_revision(workflow, note)
        job_result.log(message=f"Archived '{workflow.key}'. {note}")


def _errors(parsed, repository):
    if not isinstance(parsed, dict) or not parsed.get("key"):
        return [{"pointer": "", "message": "workflow file must be a mapping with a key"}]
    try:
        canonical = normalize(parsed)
    except Exception as exc:
        return [{"pointer": "", "message": str(exc)}]
    return validate(canonical, repo=repository, enforce_policy=False, check_scripts=True)


def _mark_invalid(workflow, errors, touch, job_result):
    rendered = "; ".join(f"{item.get('pointer') or ''}: {item.get('message')}" for item in errors)
    author = touch.get("author") or "unknown"
    workflow.git_status = GIT_STATUS_GIT_INVALID
    workflow.git_error = f"{rendered} (commit {touch.get('sha') or '?'} by {author})"[:4000]
    if touch.get("sha"):
        workflow.git_commit = touch["sha"]
    workflow.save(update_fields=["git_status", "git_error", "git_commit", "last_updated"])
    job_result.log(message=f"E-NWE-103 Git version of '{workflow.key}' is invalid. {workflow.git_error}")


def _apply(repository, workflow, parsed, raw_bytes, touch, relative_path, job_result, supersede, reason=""):
    from nautobot_workflow_engine.datasources import apply_definition

    canonical = normalize(parsed)
    # enabled is Nautobot-only after the first import. A later file does not flip the switch.
    enabled = workflow.enabled
    saved = apply_definition(
        canonical,
        source_repo=repository,
        job_result=job_result,
        managed_by=MANAGED_BY_NAUTOBOT,
        enabled=enabled,
        workflow=workflow,
    )
    _record(saved, canonical, touch, raw_bytes, relative_path, supersede, reason)
    job_result.log(
        message=(
            f"Applied Git change to '{saved.key}' from {touch.get('sha') or 'unknown'} "
            f"by {touch.get('author') or 'unknown'}."
        )
    )


def _record(workflow, canonical, touch, raw_bytes, relative_path, supersede, reason):
    text = to_yaml(canonical)
    digest = content_hash(text)
    published = workflow.published_revision if workflow.published_revision_id else None
    if (
        published is not None
        and published.content_hash == digest
        and published.state == WorkflowRevision.STATE_PUBLISHED
    ):
        workflow.git_status = GIT_STATUS_IN_SYNC
        workflow.git_content_hash = file_digest(raw_bytes)
        workflow.git_path = relative_path or workflow.git_path
        workflow.git_error = ""
        workflow.git_retry_count = 0
        workflow.git_next_retry_at = None
        if touch.get("sha"):
            workflow.git_commit = touch["sha"]
        workflow.save(
            update_fields=[
                "git_status",
                "git_content_hash",
                "git_path",
                "git_error",
                "git_retry_count",
                "git_next_retry_at",
                "git_commit",
                "last_updated",
            ]
        )
        return
    if published is not None:
        published.state = WorkflowRevision.STATE_SUPERSEDED
        published.superseded_reason = reason or (
            f"newer Git change {touch.get('sha') or ''} by {touch.get('author') or 'unknown'}"
        )
        published.save(update_fields=["state", "superseded_reason"])
    latest = workflow.revisions.order_by("-number").values_list("number", flat=True).first() or 0
    revision = workflow.revisions.create(
        number=latest + 1,
        state=WorkflowRevision.STATE_PUBLISHED,
        source=WorkflowRevision.SOURCE_GIT,
        definition=canonical,
        yaml=text,
        content_hash=digest,
        published_at=touch.get("at") or timezone.now(),
        git_commit=touch.get("sha") or "",
        git_author=touch.get("author") or "",
        comment=(reason or "Imported from Git.")[:200],
    )
    workflow.published_revision = revision
    workflow.git_status = GIT_STATUS_IN_SYNC
    workflow.git_content_hash = file_digest(raw_bytes)
    workflow.git_path = relative_path or workflow.git_path
    workflow.git_commit = touch.get("sha") or workflow.git_commit
    workflow.git_error = ""
    workflow.git_retry_count = 0
    workflow.git_next_retry_at = None
    workflow.managed_by = MANAGED_BY_NAUTOBOT
    workflow.save()


def _note_revision(workflow, note):
    published = workflow.published_revision if getattr(workflow, "published_revision_id", None) else None
    if published is None:
        return
    published.comment = note[:200]
    published.save(update_fields=["comment"])


def _published_at(workflow):
    revision = workflow.published_revision if getattr(workflow, "published_revision_id", None) else None
    if revision is None:
        return None
    return revision.published_at


def relative_workflow_path(path, repo_path):
    """Return a repository-relative posix path."""
    try:
        return Path(path).resolve().relative_to(Path(repo_path).resolve()).as_posix()
    except Exception:
        return str(path)
