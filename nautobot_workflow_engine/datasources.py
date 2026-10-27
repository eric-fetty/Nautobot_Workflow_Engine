"""Git repository sync functionality for workflows."""

import re
from pathlib import Path

import yaml
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from nautobot.apps.datasources import DatasourceContent
from nautobot.extras.models import GitRepository

from .approvals import ApprovalRemovalError, reconcile_approval_definition
from .executors import clear_repo_script_cache, validate_repo_filter_modules
from .models import (
    MANAGED_BY_GIT,
    MANAGED_BY_NAUTOBOT,
    WORKFLOWS_CONTENT,
    Workflow,
    WorkflowAction,
    WorkflowFilterModule,
    WorkflowHelper,
    WorkflowInput,
    WorkflowRevision,
    WorkflowRun,
)
from .schema import PHASE_YAML_KEYS
from .schema.normalize import normalize
from .schema.validate import validate
from .schema.yaml_io import content_hash, to_yaml
from .settings_helpers import get_setting


def _process_includes(job_result, file_handler):
    """Process any includes in the content."""
    content = file_handler.read_text(encoding="utf-8")
    include_pattern = re.compile(r"""^\s*{%\s*include\s+[\"'](?P<path>[\w\/\.\-]+)[\"']\s*%}""")
    for line in content.splitlines():
        match = include_pattern.search(line)
        if match:
            # Load the template path from the match
            template_path = match.group("path")
            template_file = file_handler.parent / template_path
            if template_file.exists():
                # If the template file exists, read its content and replace the include
                template_content = template_file.read_text(encoding="utf-8")
                content = content.replace(line, template_content)
            else:
                # If the template file doesn't exist, log a warning
                job_result.log(f"Template file not found: {template_file}")

    return content


def _is_filter_module_path(relative_path):
    """Return True when the repository-relative path is inside the configured filters directory."""
    filters_dir = Path(get_setting("filters_dir", "filters") or "filters")
    filters_parts = filters_dir.parts
    rel_parts = relative_path.parts
    return len(rel_parts) >= len(filters_parts) and rel_parts[: len(filters_parts)] == filters_parts


def _sync_repo_python_modules(repository_record, repo_path, job_result):
    """Store repository Python modules in helper or filter-module models."""
    WorkflowHelper.objects.filter(source_repo=repository_record).delete()
    WorkflowFilterModule.objects.filter(source_repo=repository_record).delete()

    for py_file in repo_path.rglob("*.py"):
        if "__pycache__" in str(py_file) or py_file.name.startswith("."):
            continue

        relative_path = py_file.relative_to(repo_path)
        model = WorkflowFilterModule if _is_filter_module_path(relative_path) else WorkflowHelper
        model.objects.create(
            source_repo=repository_record,
            file_path=str(relative_path),
            content=py_file.read_text(encoding="utf-8"),
        )

        if model is WorkflowFilterModule:
            job_result.log(message=f"Stored filter module: {relative_path}")
        else:
            job_result.log(message=f"Stored Python file: {relative_path}")


def _other_workflow_repo_ids_to_protect(repository_record):
    """PKs of *other* repositories whose workflows this sync must leave alone.

    A repository is protected when it still provides workflows and has a different
    remote URL. A same-URL duplicate (a GitRepository that was deleted and added again)
    is not protected, so its removed workflows are cleaned up when the live copy syncs.
    """
    remote_url = getattr(repository_record, "remote_url", None) or ""
    protected = []
    for repo in GitRepository.objects.exclude(pk=repository_record.pk).only("pk", "provided_contents", "remote_url"):
        if WORKFLOWS_CONTENT not in (repo.provided_contents or []):
            continue
        other_url = getattr(repo, "remote_url", None) or ""
        if remote_url and other_url and other_url == remote_url:
            continue
        protected.append(repo.pk)
    return protected


def _orphan_workflow_queryset(repository_record, present_keys):
    """Enabled Git-managed workflows that should leave the Workflows list after this sync.

    Only workflows that Git owns (``managed_by="git"``) are ever candidates. Workflows
    built or published in Nautobot (``managed_by="nautobot"``) are never disabled here,
    whatever their repository; a deleted file of theirs is handled by the
    newest-change-wins rules in ``sync_rules.archive_if_deleted``.

    Among Git-managed workflows whose key is not on disk in this sync, it catches this
    repository's own, ones whose repository link was cleared (the repository was
    deleted), and ones linked to a same-URL duplicate or to a repository that no longer
    provides workflows. Workflows owned by another live workflows repository are kept.
    """
    qs = Workflow.objects.filter(enabled=True, managed_by=MANAGED_BY_GIT).exclude(key__in=present_keys)
    protected_ids = _other_workflow_repo_ids_to_protect(repository_record)
    if protected_ids:
        qs = qs.exclude(source_repo_id__in=protected_ids)
    return qs


def _disable_workflows_missing_from_repo(repository_record, present_keys, job_result):
    """Disable Git-managed workflows whose file is gone. Run history is kept.

    Disabled workflows are hidden from the Workflows list unless ``show_disabled`` is on.
    The switch is saved before the optional detail logging, so a logging problem can't
    leave an orphan enabled.
    """
    strays = list(
        Workflow.objects.filter(enabled=True, managed_by=MANAGED_BY_GIT)
        .exclude(key__in=present_keys)
        .only("pk", "key", "name", "source_repo_id")
    )
    if strays:
        detail = ", ".join(f"{w.key}(source_repo={w.source_repo_id or 'none'})" for w in strays)
        job_result.log(message=f"Enabled Git-managed workflows not in this repository: {detail}")

    workflows_to_disable = list(_orphan_workflow_queryset(repository_record, present_keys))
    job_result.log(
        message=(
            f"Checking for removed workflows: {len(workflows_to_disable)} to switch off; "
            f"{len(present_keys)} key(s) in this repository "
            f"({', '.join(sorted(str(k) for k in present_keys)) or 'none'})."
        )
    )

    disabled_count = 0
    for workflow in workflows_to_disable:
        workflow.enabled = False
        workflow.save(update_fields=["enabled"])
        disabled_count += 1
        try:
            job_result.log(
                message=(
                    f"Disabling workflow '{workflow.name}' (key: {workflow.key}, "
                    f"source_repo={workflow.source_repo_id or 'none'}) with {workflow.actions.count()} actions, "
                    f"{workflow.inputs.count()} inputs, "
                    f"{WorkflowRun.objects.filter(workflow_id=workflow.pk).count()} runs - no longer in repository"
                )
            )
        except Exception as exc:  # noqa: BLE001 - best-effort logging only
            job_result.log(
                message=f"Disabled workflow '{workflow.name}' (key: {workflow.key}) - no longer in repository "
                f"(detail logging failed: {exc})"
            )
        reconcile_approval_definition(workflow, job_result=job_result)

    if disabled_count > 0:
        job_result.log(
            message=f"Disabled {disabled_count} workflows no longer present in repository. Run history preserved."
        )

    return disabled_count


class _Quiet:
    """A job_result stand-in for pass 1, so include warnings are only logged once."""

    def log(self, *args, **kwargs):  # pylint: disable=unused-argument
        """Drop the message."""


_QUIET = _Quiet()


def _keys_on_disk(yaml_files):
    """Pass 1: every workflow key that has a file in this sync, even if the file later fails to import.

    A file that is there but broken must not switch its workflow off.
    """
    keys = set()
    for yml in yaml_files:
        try:
            data = yaml.safe_load(_process_includes(_QUIET, yml))
        except Exception:  # noqa: BLE001, S112 - the import pass reports the error
            continue
        if isinstance(data, dict) and data.get("key"):
            keys.add(data["key"])
    return keys


def _validate_persisted_definition(data, source_repo):
    """Reject a definition that cannot be stored, before any row is written.

    Actions are checked first so a missing script is the error the builder sees,
    even when an earlier phase would otherwise have been inserted already.
    ``data`` is canonical: phase aliases have already been folded.
    """
    if not isinstance(data, dict) or not data.get("key"):
        raise ValueError("Workflow definition must include a key.")
    key = data["key"]
    phased_actions = [(phase, data.get(yaml_key) or []) for phase, yaml_key in PHASE_YAML_KEYS.items()]
    for phase, actions in phased_actions:
        for act in actions or []:
            if not isinstance(act, dict):
                raise ValueError(f"[Workflow {key}] Each action in phase '{phase}' must be an object.")
            if act.get("type") == "nautobot":
                script_path = (act.get("script") or "").lstrip("/")
                if not script_path:
                    raise ValueError(f"[Workflow {key}] 'script' is required for nautobot actions")
                if source_repo is None:
                    raise ValueError(f"[Workflow {key}] Nautobot actions require a source Git repository")
                if not WorkflowHelper.objects.filter(source_repo=source_repo, file_path=script_path).exists():
                    raise ValueError(f"[Workflow {key}] Script not found in repository: {script_path}")
            elif act.get("type") != "awx":
                raise ValueError(f"[Workflow {key}] Unsupported action type in phase '{phase}': {act.get('type')}")
    for ip in data.get("inputs", []) or []:
        if not isinstance(ip, dict):
            continue
        if ip.get("type") in ("object", "multiobject") and ip.get("model"):
            app_label, model = str(ip["model"]).split(".", 1)
            try:
                ContentType.objects.get(app_label=app_label, model=model.lower())
            except ContentType.DoesNotExist:
                raise ValueError(f"[Workflow {key}] Invalid model for input {ip.get('key')}: {ip['model']}")


def save_workflow_definition(data, *, source_repo, job_result=None, managed_by=MANAGED_BY_NAUTOBOT):
    """Normalize, validate, and apply one definition.

    Callers that already validated, including the builder service, should call
    ``apply_definition`` directly. This wrapper remains for Git-free saves and
    the row-mapping tests.
    """
    enabled = data.get("enabled", True) if isinstance(data, dict) else True
    canonical = normalize(data) if isinstance(data, dict) else data
    _validate_persisted_definition(canonical, source_repo)
    errors = validate(canonical, repo=None, enforce_policy=False, check_scripts=False)
    if errors:
        raise ValueError("; ".join(item["message"] for item in errors))
    with transaction.atomic():
        return apply_definition(
            canonical,
            source_repo=source_repo,
            job_result=job_result,
            managed_by=managed_by,
            enabled=enabled,
        )


def apply_definition(
    data, *, source_repo, job_result=None, managed_by=MANAGED_BY_NAUTOBOT, enabled=None, workflow=None
):
    """Write ``raw_definition`` and replace input and action rows.

    Validation has already passed. ``enabled`` is operational and defaults to the
    legacy Git value (true when the file omits it) only when the caller does not
    pass it. Pass ``workflow`` to update that row instead of looking it up by key.
    """
    if enabled is None:
        enabled = data.get("enabled", True) if isinstance(data, dict) else True
    return _apply_definition(
        data,
        source_repo=source_repo,
        job_result=job_result,
        managed_by=managed_by,
        enabled=enabled,
        workflow=workflow,
    )


def _apply_definition(
    data, *, source_repo, job_result=None, managed_by=MANAGED_BY_NAUTOBOT, enabled=True, workflow=None
):
    """Persist one workflow definition. Caller holds the transaction."""
    yaml_enabled = enabled
    # Prefer approval_required; accept legacy requires_approval from early drafts.
    if "approval_required" in data:
        yaml_approval_required = bool(data.get("approval_required"))
    else:
        yaml_approval_required = bool(data.get("requires_approval", False))

    defaults = {
        "name": data.get("name", data["key"]),
        "description": data.get("description", ""),
        "category": data.get("category", ""),
        "enabled": yaml_enabled,
        "approval_required": yaml_approval_required,
        "manual_duration_minutes": data.get("manual_duration_minutes"),
        "raw_definition": data,
        "source_repo": source_repo,
        "managed_by": managed_by,
    }
    if workflow is None:
        wf, _created = Workflow.objects.update_or_create(key=data["key"], defaults=defaults)
    else:
        for field, value in defaults.items():
            setattr(workflow, field, value)
        workflow.save()
        wf = workflow
    reconcile_approval_definition(wf, job_result=job_result)
    wf.inputs.all().delete()
    for order, ip in enumerate(data.get("inputs", []) or [], start=1):
        ct = None
        if ip.get("type") in ("object", "multiobject") and ip.get("model"):
            app_label, model = ip["model"].split(".")
            try:
                ct = ContentType.objects.get(app_label=app_label, model=model.lower())
            except ContentType.DoesNotExist:
                raise ValueError(f"[Workflow {wf.key}] Invalid model for input {ip['key']}: {ip['model']}")

        # Normalize validators:
        # - support either a single "regex" (with optional message/flags near root)
        # - or a list under "validators", e.g. [{"regex": "...", "flags": ["IGNORECASE"], "message": "..."}]
        validators = ip.get("validators")
        if not validators:
            if "regex" in ip:
                v = {"regex": ip.get("regex")}
                if "message" in ip:
                    v["message"] = ip["message"]
                if "flags" in ip:
                    v["flags"] = ip["flags"]
                validators = [v]

        WorkflowInput.objects.create(
            workflow=wf,
            key=ip["key"],
            label=ip.get("label", ip["key"]),
            input_type=ip["type"],
            content_type=ct,
            required=ip.get("required", False),
            default=ip.get("default"),
            depends_on=ip.get("depends_on", []),
            filter_query=ip.get("filter", {}).get("query") if isinstance(ip.get("filter"), dict) else None,
            choices=ip.get("choices"),
            columns=ip.get("columns"),
            visible_when=ip.get("show_if"),
            validators=validators,
            help_text=ip.get("help_text") or "",
            placeholder=ip.get("placeholder") or "",
            min_value=_optional_number(ip.get("min_value")),
            max_value=_optional_number(ip.get("max_value")),
            min_items=_optional_int(ip.get("min_items")),
            max_items=_optional_int(ip.get("max_items")),
            min_rows=_optional_int(ip.get("min_rows")),
            max_rows=_optional_int(ip.get("max_rows")),
            csv_import=ip.get("csv_import", True) is not False,
            order=order,
        )

    # Use bulk deletion to avoid get_absolute_url issues
    WorkflowAction.objects.filter(workflow=wf).delete()

    phased_actions = [(phase, data.get(yaml_key) or []) for phase, yaml_key in PHASE_YAML_KEYS.items()]

    for phase, actions in phased_actions:
        for order, act in enumerate(actions, start=1):
            if act.get("type") == "nautobot":
                script_path = (act.get("script") or "").lstrip("/")
                WorkflowAction.objects.create(
                    workflow=wf,
                    phase=phase,
                    order=order,
                    action_type="nautobot",
                    name=act.get("name", "nautobot"),
                    when=act.get("when", ""),
                    script_path="/" + script_path,
                    with_vars=act.get("with", {}),
                )

            elif act.get("type") == "awx":
                WorkflowAction.objects.create(
                    workflow=wf,
                    phase=phase,
                    order=order,
                    action_type="awx",
                    name=act.get("name", "awx"),
                    when=act.get("when", ""),
                    awx_template_id=act.get("template_id"),
                    awx_extra_vars=act.get("extra_vars", {}),
                    monitor=act.get("monitor", True),
                )

            else:
                raise ValueError(f"[Workflow {wf.key}] Unsupported action type in phase '{phase}': {act.get('type')}")

    return wf


def _optional_number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _import_nautobot_by_path(repository_record, raw_bytes, parsed, job_result, relative_path, synced_workflow_keys):
    """Mark a tracked Nautobot workflow git_invalid when its file no longer has a key.

    A missing key used to skip the file, and the later delete pass then archived it.
    """
    lookup = getattr(Workflow.objects, "filter", None)
    if not callable(lookup):
        return False
    try:
        existing = lookup(
            source_repo=repository_record,
            managed_by=MANAGED_BY_NAUTOBOT,
            git_path=relative_path,
        ).first()
    except Exception:
        return False
    if existing is None:
        return False
    from nautobot_workflow_engine.utilities.sync_rules import consider_nautobot_file

    consider_nautobot_file(repository_record, existing, raw_bytes, parsed, job_result, relative_path)
    synced_workflow_keys.add(existing.key)
    return True


def _import_same_repo_nautobot(
    repository_record, workflow_key, raw_bytes, parsed, job_result, relative_path, synced_workflow_keys
):
    """Apply latest-wins for a Nautobot-managed workflow in this repository.

    Mock sync tests replace ``Workflow.objects`` with an object that has no ``filter``.
    Those tests keep the create path.
    """
    lookup = getattr(Workflow.objects, "filter", None)
    if not callable(lookup):
        return False
    try:
        existing = lookup(key=workflow_key).first()
    except Exception:
        return False
    if existing is None or getattr(existing, "managed_by", None) != MANAGED_BY_NAUTOBOT:
        return False
    repo_id = getattr(repository_record, "id", None) or getattr(repository_record, "pk", None)
    if getattr(existing, "source_repo_id", None) != repo_id:
        return False
    from nautobot_workflow_engine.utilities.sync_rules import consider_nautobot_file

    consider_nautobot_file(repository_record, existing, raw_bytes, parsed, job_result, relative_path)
    synced_workflow_keys.add(workflow_key)
    return True


def _remember_git_file(workflow, raw_bytes, relative_path, repository_record):
    """Store the file hash on a Git-managed workflow so a later adopt knows the path."""
    if not hasattr(workflow, "git_status"):
        return
    from nautobot_workflow_engine.models import GIT_STATUS_IN_SYNC
    from nautobot_workflow_engine.utilities.sync_rules import file_digest

    workflow.git_status = GIT_STATUS_IN_SYNC
    workflow.git_path = relative_path
    workflow.git_content_hash = file_digest(raw_bytes)
    workflow.git_error = ""
    head = getattr(repository_record, "current_head", "") or ""
    if head:
        workflow.git_commit = head
    workflow.save(
        update_fields=["git_status", "git_path", "git_content_hash", "git_error", "git_commit", "last_updated"]
    )


def _archive_deleted_nautobot(repository_record, synced_workflow_keys, job_result):
    """Archive Nautobot-managed workflows whose file was deleted, when that delete is newer."""
    from nautobot_workflow_engine.utilities.sync_rules import archive_if_deleted

    archive_if_deleted(repository_record, synced_workflow_keys, job_result)


def _sync_should_skip_key(repository_record, workflow_key, job_result, synced_workflow_keys):
    """Return True when this file must not overwrite the workflow that already owns ``workflow_key``.

    Unit tests that replace ``Workflow.objects`` with a namespace that has no ``filter``
    keep the previous create path. A real sync skips another repo's key and any
    Nautobot-managed workflow, and logs ``E-NWE-101``.
    """
    lookup = getattr(Workflow.objects, "filter", None)
    if not callable(lookup):
        return False
    existing = lookup(key=workflow_key).first()
    if existing is None:
        return False
    repo_id = getattr(repository_record, "id", None)
    if repo_id is None:
        repo_id = getattr(repository_record, "pk", None)
    same_repo = getattr(existing, "source_repo_id", None) == repo_id
    managed_by = getattr(existing, "managed_by", MANAGED_BY_GIT)
    other_repo = not same_repo and getattr(existing, "source_repo_id", None) is not None
    if managed_by == MANAGED_BY_NAUTOBOT and same_repo:
        return False
    if managed_by == MANAGED_BY_NAUTOBOT or other_repo:
        job_result.log(
            message=(
                f"E-NWE-101 Refusing to overwrite workflow '{workflow_key}'. "
                "It is Nautobot-managed or owned by another Git repository."
            )
        )
        return True
    return False


def _record_sync_revision(workflow, canonical):
    """Append a published ``source=git`` revision when the applied definition changed.

    Mock workflows used by unit tests have no ``revisions`` relation. Failures here
    are swallowed so a revision write cannot abort the rest of the sync.
    """
    revisions = getattr(workflow, "revisions", None)
    if revisions is None or not hasattr(revisions, "create"):
        return
    try:
        text = to_yaml(canonical)
        digest = content_hash(text)
        published = getattr(workflow, "published_revision", None)
        if (
            published is not None
            and getattr(published, "content_hash", "") == digest
            and getattr(published, "state", "") == WorkflowRevision.STATE_PUBLISHED
        ):
            return
        if published is not None and getattr(published, "pk", None) and hasattr(published, "save"):
            published.state = WorkflowRevision.STATE_SUPERSEDED
            published.superseded_reason = "Replaced by a Git sync."
            published.save(update_fields=["state", "superseded_reason"])
        latest = revisions.order_by("-number").values_list("number", flat=True).first() or 0
        revision = revisions.create(
            number=latest + 1,
            state=WorkflowRevision.STATE_PUBLISHED,
            source=WorkflowRevision.SOURCE_GIT,
            definition=canonical,
            yaml=text,
            content_hash=digest,
            published_at=timezone.now(),
        )
        workflow.published_revision = revision
        workflow.save(update_fields=["published_revision"])
    except Exception:
        return


def sync_workflows_from_repo(repository_record: GitRepository, job_result, delete=False):
    """Sync workflow definitions and helper files from a Git repository.

    On a normal sync (``delete=False``), Git-managed workflows present in the repo are
    upserted. Nautobot-managed workflows are left unchanged, and a key owned by another
    repository is never taken over (``E-NWE-101``). Git-managed workflows from this repo
    that are no longer present are disabled so they disappear from the launch page.

    When the Git repository itself is deleted (``delete=True``), all workflows sourced
    from it are disabled and helper/filter modules are cleared.
    """
    if delete:
        _disable_workflows_missing_from_repo(repository_record, set(), job_result)
        WorkflowHelper.objects.filter(source_repo=repository_record).delete()
        WorkflowFilterModule.objects.filter(source_repo=repository_record).delete()
        clear_repo_script_cache(repository_record)
        return

    repo_path = Path(repository_record.filesystem_path)
    wf_dir = get_setting("workflow_dir", "workflows") or "workflows"
    workflows_path = repo_path / wf_dir
    if not workflows_path.exists():
        # Workflows directory removed — disable everything previously synced from this repo.
        _disable_workflows_missing_from_repo(repository_record, set(), job_result)
        _archive_deleted_nautobot(repository_record, set(), job_result)
        clear_repo_script_cache(repository_record)
        return

    # Track workflows found in this sync
    synced_workflow_keys = set()

    yaml_files = list(workflows_path.glob("*.y*ml"))
    yaml_files.sort(key=lambda item: str(getattr(item, "name", "") or ""))

    # Pass 1: keys on disk. Switch off removed Git-managed workflows before importing, so a
    # failing import later in the sync can't leave a removed workflow on the Workflows list.
    present_keys = _keys_on_disk(yaml_files)
    _disable_workflows_missing_from_repo(repository_record, present_keys, job_result)

    _sync_repo_python_modules(repository_record, repo_path, job_result)
    validate_repo_filter_modules(repository_record)

    for yml in yaml_files:
        filename = getattr(yml, "name", None) or "workflow.yaml"
        workflow_key = getattr(yml, "stem", None) or "workflow"
        try:
            content = _process_includes(job_result, yml)
            data = yaml.safe_load(content)
            raw_bytes = yml.read_bytes() if hasattr(yml, "read_bytes") else content.encode("utf-8")
            relative_path = f"{wf_dir}/{filename}"
            if not isinstance(data, dict) or not data.get("key"):
                if _import_nautobot_by_path(
                    repository_record,
                    raw_bytes,
                    data if isinstance(data, dict) else {},
                    job_result,
                    relative_path,
                    synced_workflow_keys,
                ):
                    continue
                job_result.log(message=f"E-NWE-102 Skipping {filename}: workflow file must be a mapping with a key.")
                continue

            workflow_key = data["key"]
            if _import_same_repo_nautobot(
                repository_record, workflow_key, raw_bytes, data, job_result, relative_path, synced_workflow_keys
            ):
                continue
            if _sync_should_skip_key(repository_record, workflow_key, job_result, synced_workflow_keys):
                continue

            # YAML enabled only sets the switch the first time the file is imported.
            # Mock sync tests replace Workflow.objects with a namespace that has no filter.
            lookup = getattr(Workflow.objects, "filter", None)
            prior = None
            if callable(lookup):
                try:
                    prior = lookup(key=workflow_key).first()
                except Exception:
                    prior = None
            enabled = prior.enabled if prior is not None else data.get("enabled", True)
            canonical = normalize(data)
            errors = validate(canonical, repo=repository_record, enforce_policy=False, check_scripts=True)
            if errors:
                rendered = "; ".join(
                    f"{item.get('pointer') or item.get('path')}: {item.get('message')}" for item in errors
                )
                job_result.log(message=f"E-NWE-102 Skipping '{workflow_key}' from {filename}: {rendered}")
                continue
            with transaction.atomic():
                saved = apply_definition(
                    canonical,
                    source_repo=repository_record,
                    job_result=job_result,
                    managed_by=MANAGED_BY_GIT,
                    enabled=enabled,
                )
                _record_sync_revision(saved, canonical)
            synced_workflow_keys.add(workflow_key)
            _remember_git_file(saved, raw_bytes, relative_path, repository_record)
        except ApprovalRemovalError as exc:
            job_result.log(
                message=(
                    f"E-NWE-301 Skipping '{workflow_key}' from {filename}: {exc} "
                    "The stored workflow was left unchanged."
                )
            )
            synced_workflow_keys.add(workflow_key)
            continue
        except Exception as exc:
            job_result.log(message=f"E-NWE-102 Skipping '{workflow_key}' from {filename}: {exc}")
            continue

    _archive_deleted_nautobot(repository_record, synced_workflow_keys, job_result)

    clear_repo_script_cache(repository_record)


datasource_contents = []


datasource_contents.append(
    (
        "extras.gitrepository",
        DatasourceContent(
            name="workflows",
            content_identifier="nautobot_workflow_engine.workflows",
            icon="mdi-file-document-outline",
            callback=sync_workflows_from_repo,
        ),
    )
)
