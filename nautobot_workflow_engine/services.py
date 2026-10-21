"""Draft, publish, and the database half of the stale-edit check.

Git sync and the builder API both end in ``apply_definition``. A draft never
calls it. M3 adds the Git probe inside ``check_stale_git``; until then that
function returns None and a save is not blocked on Git.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .approvals import ApprovalRemovalError, reconcile_approval_definition
from .datasources import apply_definition
from .models import MANAGED_BY_NAUTOBOT, Workflow, WorkflowRevision
from .schema.builder_payload import actions_require_repository
from .schema.policy import AUTHOR_CODE_MESSAGE, code_changes
from .schema.validate import validate
from .schema.yaml_io import content_hash, to_yaml

logger = logging.getLogger(__name__)

PUBLISH_MESSAGE = "You need the *Publish workflows* permission to change this field."
SCOPE_MESSAGE = "This change would move the workflow outside what you're allowed to edit (for example the category)"


class ServiceError(Exception):
    """A builder write the API can turn into 400, 403, or 409."""

    def __init__(self, status, **payload):
        """Store the HTTP status and the response body."""
        self.status = status
        self.payload = payload
        super().__init__(payload.get("detail") or payload.get("message") or status)


def stale_revision_detail(author, when_text):
    """Message for a save that started from an older Nautobot revision."""
    return f"Updated in Nautobot by {author} at {when_text}; reload to continue."


def stale_git_detail(author, when_text):
    """Message M3 returns when Git moved the file after the editor opened."""
    return f"Updated in Git by {author} at {when_text}; reload to continue."


def check_stale_git(workflow, base_git_commit, base_content_hash):
    """Return a ``stale_git`` 409 payload, a ``git_unverified`` warning, or None.

    A warning has no ``code``. Callers must only reject the save when ``code`` is set.
    An unreachable remote does not block the save.
    """
    if workflow is None or not getattr(workflow, "source_repo_id", None):
        return None
    status = getattr(workflow, "git_status", "") or ""
    tracked = bool(getattr(workflow, "git_commit", "")) or status not in ("", "not_configured", "disabled_repo")
    if not tracked:
        return None
    base_commit = getattr(workflow, "git_commit", "") or ""
    if base_git_commit not in (None, ""):
        base_commit = base_git_commit
    base_hash = getattr(workflow, "git_content_hash", "") or ""
    if base_content_hash not in (None, ""):
        base_hash = base_content_hash
    from nautobot_workflow_engine.utilities.git_probe import GitRemoteProbe
    from nautobot_workflow_engine.utilities.git_publish import (
        default_git_path,
        enqueue_repository_sync,
        known_secrets,
        sanitize_git_error,
    )

    repository = workflow.source_repo
    try:
        probe = GitRemoteProbe(repository)
        head = probe.remote_head()
        if head and head == base_commit:
            return None
        state = probe.read_path(default_git_path(workflow))
    except Exception as exc:
        return {"warning": "git_unverified", "reason": sanitize_git_error(exc, known_secrets(repository))}
    if (state.blob_hash or "") == (base_hash or ""):
        return None
    try:
        enqueue_repository_sync(repository, None)
    except Exception:
        logger.info("Could not enqueue a refresh after a stale Git check.", exc_info=True)
    author = state.author or "someone"
    when_text = _format_time(state.committed_at)
    return {
        "code": "stale_git",
        "detail": stale_git_detail(author, when_text),
        "author": author,
        "time": when_text,
        "commit": state.commit or state.head,
        "current_revision": _head(workflow).number if _head(workflow) else None,
        "base_revision": None,
    }


def check_stale(
    workflow, *, base_revision, saving_draft, base_git_commit=None, base_content_hash=None, require_base=True
):
    """Return a 409 payload when ``base_revision`` is not the current head.

    The head is the open draft when one exists, otherwise the published revision.
    ``base_git_commit`` and ``base_content_hash`` are accepted now so M3 can
    compare them without changing the callers.
    """
    head = _head(workflow)
    if head is None:
        if base_revision not in (None, "", 0, "0"):
            return _stale_payload(None, base_revision)
        return check_stale_git(workflow, base_git_commit, base_content_hash)
    if not require_base and base_revision in (None, ""):
        return check_stale_git(workflow, base_git_commit, base_content_hash)
    try:
        sent = int(base_revision)
    except (TypeError, ValueError):
        sent = None
    if sent != head.number:
        return _stale_payload(head, base_revision)
    if not saving_draft and workflow.draft_revision is not None and head.state != WorkflowRevision.STATE_DRAFT:
        return _stale_payload(workflow.draft_revision, base_revision)
    return check_stale_git(workflow, base_git_commit, base_content_hash)


def _head(workflow):
    """Return the revision a save must name: the draft if there is one, else the published revision."""
    if workflow is None:
        return None
    draft = workflow.draft_revision
    if draft is not None:
        return draft
    return workflow.published_revision


def _stale_payload(head, base_revision):
    if head is None:
        author = "someone"
        when_text = ""
        number = None
    else:
        user = (
            head.published_by
            if head.state == WorkflowRevision.STATE_PUBLISHED and head.published_by_id
            else head.created_by
        )
        author = getattr(user, "username", None) or "someone"
        moment = (
            head.published_at if head.state == WorkflowRevision.STATE_PUBLISHED and head.published_at else head.created
        )
        when_text = _format_time(moment)
        number = head.number
    return {
        "code": "stale_revision",
        "detail": stale_revision_detail(author, when_text),
        "author": author,
        "time": when_text,
        "commit": None,
        "current_revision": number,
        "base_revision": base_revision,
    }


def _format_time(moment):
    if moment is None:
        return ""
    local = timezone.localtime(moment)
    zone = local.tzname() or ""
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{clock} ({zone})" if zone else clock


def create_draft(
    *, user, definition, repository=None, comment="", source=WorkflowRevision.SOURCE_BUILDER, key=None, name=None
):
    """Create a Nautobot-managed workflow and its first draft. Runtime rows are not written."""
    if not user.has_perm("nautobot_workflow_engine.add_workflow"):
        raise ServiceError(403, detail="You need permission to add a workflow.")
    workflow_key = key or definition.get("key")
    if Workflow.objects.filter(key=workflow_key).exists():
        raise ServiceError(
            400,
            errors=[
                {
                    "pointer": "/key",
                    "path": "key",
                    "code": "duplicate_key",
                    "message": "A workflow with this key already exists.",
                }
            ],
        )
    _raise_validation(definition, repository)
    _require_author(user, None, {}, definition)
    with transaction.atomic():
        workflow = Workflow.objects.create(
            key=workflow_key,
            name=(name or definition.get("name") or workflow_key)[:200],
            description=definition.get("description") or "",
            category=definition.get("category") or "",
            enabled=False,
            approval_required=False,
            raw_definition={},
            source_repo=repository,
            managed_by=MANAGED_BY_NAUTOBOT,
            manual_duration_minutes=definition.get("manual_duration_minutes"),
        )
        revision = _insert_revision(
            workflow,
            definition,
            state=WorkflowRevision.STATE_DRAFT,
            source=source,
            user=user,
            comment=comment,
            base=None,
        )
        _assert_scope(user, workflow, "add")
    return workflow, revision


def update_draft(
    *,
    user,
    workflow,
    definition,
    base_revision,
    comment="",
    base_git_commit=None,
    base_content_hash=None,
    require_base=True,
    source=WorkflowRevision.SOURCE_BUILDER,
):
    """Replace the shared draft. The published definition and the rows stay as they are."""
    if workflow.managed_by != MANAGED_BY_NAUTOBOT:
        raise ServiceError(409, code="managed_in_git", detail="Managed in Git")
    if not user.has_perm("nautobot_workflow_engine.change_workflow", workflow):
        raise ServiceError(404, detail="Not found.")
    conflict = check_stale(
        workflow,
        base_revision=base_revision,
        saving_draft=True,
        base_git_commit=base_git_commit,
        base_content_hash=base_content_hash,
        require_base=require_base,
    )
    if conflict and conflict.get("code"):
        raise ServiceError(409, **conflict)
    _raise_validation(definition, workflow.source_repo)
    _require_author(user, workflow, _author_base(workflow), definition)
    with transaction.atomic():
        current = workflow.draft_revision
        if current is not None:
            current.state = WorkflowRevision.STATE_DISCARDED
            current.superseded_reason = "Replaced by a newer draft."
            current.save(update_fields=["state", "superseded_reason"])
        revision = _insert_revision(
            workflow,
            definition,
            state=WorkflowRevision.STATE_DRAFT,
            source=source,
            user=user,
            comment=comment,
            base=workflow.published_revision,
        )
        workflow.save(update_fields=["last_updated"])
        _assert_scope(user, workflow, "change")
    return revision


def publish(
    *,
    user,
    workflow,
    definition,
    base_revision,
    enable=None,
    commit_summary="",
    approval_confirm=False,
    repository=None,
    base_git_commit=None,
    base_content_hash=None,
    require_base=True,
    source=WorkflowRevision.SOURCE_BUILDER,
):
    """Make ``definition`` the live revision and write runtime rows."""
    if workflow is not None and workflow.managed_by != MANAGED_BY_NAUTOBOT:
        raise ServiceError(409, code="managed_in_git", detail="Managed in Git")
    conflict = check_stale(
        workflow,
        base_revision=base_revision,
        saving_draft=False,
        base_git_commit=base_git_commit,
        base_content_hash=base_content_hash,
        require_base=require_base,
    )
    warnings = []
    if conflict and conflict.get("code"):
        raise ServiceError(409, **conflict)
    if conflict and conflict.get("warning"):
        warnings.append(conflict["warning"])
    author_base = _author_base(workflow)
    repo = repository if repository is not None else getattr(workflow, "source_repo", None)
    if actions_require_repository(definition) and repo is None:
        raise ServiceError(
            400,
            errors=[
                {
                    "pointer": "/source_repo",
                    "path": "source_repo",
                    "code": "repository_required",
                    "message": "Choose a repository that contains the script.",
                }
            ],
        )
    _raise_validation(definition, repo)
    _require_author(user, workflow, author_base, definition)
    _require_publish(user, workflow, definition, enable=enable, approval_confirm=approval_confirm)
    created = workflow is None
    prior_enabled = bool(workflow.enabled) if workflow is not None else False
    prior_approval = bool(workflow.approval_required) if workflow is not None else False
    with transaction.atomic():
        if workflow is None:
            workflow = Workflow.objects.create(
                key=definition["key"],
                name=definition.get("name") or definition["key"],
                description=definition.get("description") or "",
                category=definition.get("category") or "",
                enabled=False,
                approval_required=False,
                raw_definition={},
                source_repo=repo,
                managed_by=MANAGED_BY_NAUTOBOT,
            )
        else:
            workflow.source_repo = repo
        draft = workflow.draft_revision
        previous = workflow.published_revision
        if draft is not None and (
            base_revision in (None, "") or int(base_revision) == draft.number or not require_base
        ):
            revision = draft
            revision.definition = definition
            revision.yaml = to_yaml(definition)
            revision.content_hash = content_hash(revision.yaml)
            revision.state = WorkflowRevision.STATE_PUBLISHED
        else:
            revision = _insert_revision(
                workflow,
                definition,
                state=WorkflowRevision.STATE_PUBLISHED,
                source=source,
                user=user,
                comment=commit_summary,
                base=previous,
            )
        if previous is not None and previous.pk != revision.pk:
            previous.state = WorkflowRevision.STATE_SUPERSEDED
            previous.superseded_reason = f"Published r{revision.number}."
            previous.save(update_fields=["state", "superseded_reason"])
        revision.state = WorkflowRevision.STATE_PUBLISHED
        revision.published_by = user
        revision.published_at = timezone.now()
        revision.comment = (commit_summary or revision.comment or "")[:200]
        revision.source = source
        revision.save()
        new_enabled = prior_enabled if enable is None else bool(enable)
        try:
            saved = apply_definition(
                definition,
                source_repo=repo,
                managed_by=MANAGED_BY_NAUTOBOT,
                enabled=new_enabled,
                workflow=saved_workflow_for_apply(workflow),
            )
        except ApprovalRemovalError as exc:
            raise ServiceError(
                400,
                errors=[
                    {"pointer": "/approval_required", "path": exc.path, "code": "approval_pending", "message": str(exc)}
                ],
            )
        saved.published_revision = revision
        saved.save(update_fields=["published_revision"])
        try:
            reconcile_approval_definition(saved)
        except ApprovalRemovalError as exc:
            raise ServiceError(
                400,
                errors=[
                    {"pointer": "/approval_required", "path": exc.path, "code": "approval_pending", "message": str(exc)}
                ],
            )
        _assert_scope(user, saved, "add" if created else "change")
        if _publish_scope_needed(created, prior_enabled, prior_approval, saved, enable):
            _assert_scope(user, saved, "publish")
        if code_changes(author_base, definition):
            _assert_scope(user, saved, "author_code")
        saved_id = saved.pk
        user_id = getattr(user, "pk", None)

        def _push_after_commit():
            from django.contrib.auth import get_user_model

            from nautobot_workflow_engine.utilities.git_publish import schedule_publish

            row = Workflow.objects.get(pk=saved_id)
            person = get_user_model().objects.filter(pk=user_id).first() if user_id else None
            schedule_publish(row, person)

        transaction.on_commit(_push_after_commit)
    saved._git_warnings = warnings
    return saved, revision


def saved_workflow_for_apply(workflow):
    """Let apply update this row instead of update_or_create when the caller already holds it."""
    return workflow


def discard_draft(*, user, workflow):
    """Mark the open draft discarded."""
    if not user.has_perm("nautobot_workflow_engine.change_workflow", workflow):
        raise ServiceError(404, detail="Not found.")
    draft = workflow.draft_revision
    if draft is None:
        return
    draft.state = WorkflowRevision.STATE_DISCARDED
    draft.superseded_reason = "Draft discarded."
    draft.save(update_fields=["state", "superseded_reason"])


def set_enabled(*, user, workflow, enabled):
    """Turn a published workflow on or off. This does not change the definition."""
    if not user.has_perm("nautobot_workflow_engine.publish_workflow", workflow):
        raise ServiceError(403, detail=PUBLISH_MESSAGE)
    with transaction.atomic():
        workflow.enabled = bool(enabled)
        workflow.save(update_fields=["enabled"])
        reconcile_approval_definition(workflow)
        _assert_scope(user, workflow, "publish")
    return workflow


def duplicate_workflow(*, user, source, key, name):
    """Copy a workflow into a disabled Nautobot-managed draft. The copy is not published."""
    if not user.has_perm("nautobot_workflow_engine.add_workflow"):
        raise ServiceError(403, detail="You need permission to add a workflow.")
    definition = dict(source.raw_definition or {})
    if source.draft_revision is not None and not source.published_revision_id:
        definition = dict(source.draft_revision.definition or definition)
    definition["key"] = key
    definition["name"] = name
    from .schema.normalize import normalize

    canonical = normalize(definition)
    with transaction.atomic():
        workflow = Workflow.objects.create(
            key=key,
            name=name[:200],
            description=canonical.get("description") or "",
            category=canonical.get("category") or source.category or "",
            enabled=False,
            approval_required=False,
            raw_definition={},
            source_repo=source.source_repo,
            managed_by=MANAGED_BY_NAUTOBOT,
            manual_duration_minutes=canonical.get("manual_duration_minutes"),
        )
        revision = _insert_revision(
            workflow,
            canonical,
            state=WorkflowRevision.STATE_DRAFT,
            source=WorkflowRevision.SOURCE_DUPLICATE,
            user=user,
            comment=f"Duplicated from {source.key}.",
            base=None,
        )
        _assert_scope(user, workflow, "add")
    return workflow, revision


def restore_revision(*, user, workflow, revision):
    """Copy an old revision into the shared draft."""
    if not user.has_perm("nautobot_workflow_engine.change_workflow", workflow):
        raise ServiceError(404, detail="Not found.")
    return update_draft(
        user=user,
        workflow=workflow,
        definition=revision.definition,
        base_revision=_head(workflow).number if _head(workflow) else None,
        comment=f"Restored from r{revision.number}.",
        require_base=False,
    )


def _raise_validation(definition, repo):
    errors = validate(definition, repo=repo, enforce_policy=True, check_scripts=repo is not None)
    if errors:
        raise ServiceError(400, errors=errors)


def _author_base(workflow):
    """Definition code changes are compared against. A draft counts; otherwise the published rows' definition."""
    if workflow is None:
        return {}
    draft = workflow.draft_revision
    if draft is not None:
        return draft.definition or {}
    if workflow.published_revision_id and workflow.raw_definition:
        return workflow.raw_definition
    return workflow.raw_definition or {}


def _require_author(user, workflow, previous, definition):
    changes = code_changes(previous, definition)
    if not changes:
        return
    if workflow is None:
        allowed = user.has_perm("nautobot_workflow_engine.author_code_workflow")
    else:
        allowed = user.has_perm("nautobot_workflow_engine.author_code_workflow", workflow)
    if not allowed:
        raise ServiceError(
            403,
            errors=[
                {"pointer": pointer, "path": pointer, "code": "author_code", "message": AUTHOR_CODE_MESSAGE}
                for pointer in changes
            ],
        )


def _require_publish(user, workflow, definition, *, enable, approval_confirm):
    from .approvals import approval_stage_summary, pending_approval_count

    old_enabled = bool(workflow.enabled) if workflow is not None else False
    new_enabled = old_enabled if enable is None else bool(enable)
    enabled_changed = (workflow is None and new_enabled) or (workflow is not None and old_enabled != new_enabled)
    old_approval = bool(workflow.approval_required) if workflow is not None else False
    new_approval = bool(definition.get("approval_required"))
    turning_off = workflow is not None and old_approval and not new_approval
    if enabled_changed or turning_off:
        if workflow is None:
            allowed = user.has_perm("nautobot_workflow_engine.publish_workflow")
        else:
            allowed = user.has_perm("nautobot_workflow_engine.publish_workflow", workflow)
        if not allowed:
            raise ServiceError(
                403,
                errors=[
                    {
                        "pointer": "/enabled" if enabled_changed else "/approval_required",
                        "path": "enabled" if enabled_changed else "approval_required",
                        "code": "publish",
                        "message": PUBLISH_MESSAGE,
                    }
                ],
            )
    if turning_off and workflow is not None:
        pending = pending_approval_count(workflow)
        if pending:
            raise ServiceError(
                400,
                errors=[
                    {
                        "pointer": "/approval_required",
                        "path": "approval_required",
                        "code": "approval_pending",
                        "message": f"{pending} runs are waiting for approval. Resolve them first.",
                    }
                ],
            )
        if approval_stage_summary(workflow) and not approval_confirm:
            raise ServiceError(
                400,
                errors=[
                    {
                        "pointer": "/approval_required",
                        "path": "approval_required",
                        "code": "approval_confirm",
                        "message": "Confirm turning approval off. Stages will be saved and restored if you turn it back on.",
                    }
                ],
            )


def _publish_scope_needed(created, prior_enabled, prior_approval, saved, enable):
    """Return True when the publish changed availability or turned approval off."""
    if enable is not None and prior_enabled != bool(saved.enabled):
        return True
    if created and bool(saved.enabled):
        return True
    if not created and prior_approval and not bool(saved.approval_required):
        return True
    return False


def _assert_scope(user, workflow, action):
    if not Workflow.objects.restrict(user, action).filter(pk=workflow.pk).exists():
        raise ServiceError(403, detail=SCOPE_MESSAGE)


def _insert_revision(workflow, definition, *, state, source, user, comment, base):
    number = (workflow.revisions.order_by("-number").values_list("number", flat=True).first() or 0) + 1
    text = to_yaml(definition)
    return WorkflowRevision.objects.create(
        workflow=workflow,
        number=number,
        state=state,
        source=source,
        definition=definition,
        yaml=text,
        content_hash=content_hash(text),
        base_revision=base,
        comment=(comment or "")[:200],
        created_by=user if getattr(user, "pk", None) else None,
    )


INCLUDE_WARNING = (
    "Future saves rewrite this file in canonical form; comments, key order and "
    "`{% include %}` fragments will be inlined."
)


def adopt_workflow(*, user, workflow, confirm=False):
    """Switch a Git-managed workflow to Nautobot. The first push rewrites the file."""
    from nautobot_workflow_engine.utilities.git_publish import default_git_path, repo_can_publish, schedule_publish
    from nautobot_workflow_engine.utilities.sync_rules import file_digest

    if workflow.managed_by != "git":
        raise ServiceError(400, detail="This workflow is already managed in Nautobot.")
    if not user.has_perm("nautobot_workflow_engine.change_workflow", workflow):
        raise ServiceError(404, detail="Not found.")
    if not user.has_perm("nautobot_workflow_engine.publish_workflow", workflow):
        raise ServiceError(403, detail=PUBLISH_MESSAGE)
    repository = workflow.source_repo
    if repository is None or not user.has_perm("extras.view_gitrepository", repository):
        raise ServiceError(404, detail="Not found.")
    if not repo_can_publish(repository):
        raise ServiceError(400, detail="The repository is not opted in to publishing.")
    includes = _include_paths(workflow)
    if includes and not confirm:
        return {
            "adopted": False,
            "includes": includes,
            "warning": INCLUDE_WARNING,
            "workflow": workflow,
        }
    definition = dict(workflow.raw_definition or {})
    from nautobot_workflow_engine.schema.normalize import normalize

    canonical = normalize(definition) if definition else {"key": workflow.key, "name": workflow.name}
    with transaction.atomic():
        workflow.managed_by = MANAGED_BY_NAUTOBOT
        if not workflow.git_path:
            workflow.git_path = default_git_path(workflow)
        workflow.git_content_hash = file_digest(_current_file_bytes(workflow))
        previous = workflow.published_revision
        revision = _insert_revision(
            workflow,
            canonical,
            state=WorkflowRevision.STATE_PUBLISHED,
            source=WorkflowRevision.SOURCE_GIT_IMPORT,
            user=user,
            comment="Adopted into Nautobot.",
            base=previous,
        )
        if previous is not None and previous.pk != revision.pk:
            previous.state = WorkflowRevision.STATE_SUPERSEDED
            previous.superseded_reason = "Adopted into Nautobot."
            previous.save(update_fields=["state", "superseded_reason"])
        revision.state = WorkflowRevision.STATE_PUBLISHED
        revision.published_by = user
        revision.published_at = timezone.now()
        revision.save()
        workflow.published_revision = revision
        workflow.raw_definition = canonical
        workflow.save()
        saved_id = workflow.pk
        user_id = user.pk

        def _push():
            from django.contrib.auth import get_user_model

            row = Workflow.objects.get(pk=saved_id)
            schedule_publish(row, get_user_model().objects.filter(pk=user_id).first())

        transaction.on_commit(_push)
    workflow._adopt_warning = INCLUDE_WARNING if includes else ""
    workflow._adopt_includes = includes
    return {"adopted": True, "includes": includes, "warning": workflow._adopt_warning, "workflow": workflow}


def archive_workflow(*, user, workflow, confirm_key):
    """Hide a workflow and delete its file on the next push. Runs are kept."""
    if not user.has_perm("nautobot_workflow_engine.delete_workflow", workflow):
        raise ServiceError(404, detail="Not found.")
    if confirm_key != workflow.key:
        raise ServiceError(400, detail="confirm_key must equal the workflow key.")
    with transaction.atomic():
        workflow.enabled = False
        workflow.archived = True
        workflow.save(update_fields=["enabled", "archived", "last_updated"])
        saved_id = workflow.pk
        user_id = user.pk

        def _push():
            from django.contrib.auth import get_user_model

            from nautobot_workflow_engine.utilities.git_publish import schedule_publish

            row = Workflow.objects.get(pk=saved_id)
            if row.source_repo_id:
                row.git_status = "pending"
                row.save(update_fields=["git_status", "last_updated"])
                schedule_publish(row, get_user_model().objects.filter(pk=user_id).first())

        transaction.on_commit(_push)
    return workflow


def unarchive_workflow(*, user, workflow):
    """Restore an archived workflow and publish its file again.

    ``published_at`` is the database time the push compares with the commit that
    deleted the file. The restore is a new database save, so it has to be newer
    than that commit or the next push would archive the workflow again.
    """
    if not user.has_perm("nautobot_workflow_engine.change_workflow", workflow):
        raise ServiceError(404, detail="Not found.")
    now = timezone.now()
    with transaction.atomic():
        workflow.archived = False
        workflow.enabled = True
        workflow.save(update_fields=["archived", "enabled", "last_updated"])
        revision = workflow.published_revision if workflow.published_revision_id else None
        if revision is not None:
            revision.published_at = now
            revision.save(update_fields=["published_at"])
        saved_id = workflow.pk
        user_id = user.pk

        def _push():
            from django.contrib.auth import get_user_model

            from nautobot_workflow_engine.utilities.git_publish import schedule_publish

            row = Workflow.objects.get(pk=saved_id)
            if row.source_repo_id and row.managed_by == MANAGED_BY_NAUTOBOT:
                schedule_publish(row, get_user_model().objects.filter(pk=user_id).first())

        transaction.on_commit(_push)
    return workflow


def _include_paths(workflow):
    import re

    text = ""
    payload = _current_file_bytes(workflow)
    if payload:
        text = payload.decode("utf-8", errors="replace")
    elif workflow.published_revision_id and workflow.published_revision.yaml:
        text = workflow.published_revision.yaml
    pattern = re.compile(r"""\{%\s*include\s+['"]([^'"]+)['"]""")
    return pattern.findall(text)


def _current_file_bytes(workflow):
    from pathlib import Path

    if not workflow.source_repo_id or not workflow.git_path:
        return b""
    path = Path(workflow.source_repo.filesystem_path) / workflow.git_path
    if path.is_file():
        return path.read_bytes()
    return b""
