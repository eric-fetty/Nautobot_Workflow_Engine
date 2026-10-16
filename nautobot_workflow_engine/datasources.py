"""Git repository sync functionality for workflows."""

import re
from pathlib import Path

import yaml
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.datasources import DatasourceContent
from nautobot.extras.models import GitRepository

from .approvals import reconcile_approval_definition
from .executors import clear_repo_script_cache, validate_repo_filter_modules
from .models import Workflow, WorkflowAction, WorkflowFilterModule, WorkflowHelper, WorkflowInput, WorkflowRun
from .settings_helpers import get_setting

WORKFLOW_CONTENT_IDENTIFIER = "nautobot_workflow_engine.workflows"


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


def _get_action_list(data, *keys):
    """Return first non-empty action list for any provided YAML key alias."""
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            return value
        # Be forgiving if user accidentally provides an empty scalar/object.
        return []
    return []


def _is_filter_module_path(relative_path):
    """Return True when the repository-relative path is inside the configured filters directory."""
    filters_dir = Path(get_setting("filters_dir", fallback="filters"))
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
    """Return PKs of *other* GitRepositories whose workflows this sync must not disable.

    Protects repos that still provide workflow content and use a *different* remote URL.
    Same-URL duplicates (recreated GitRepository records) are not protected, so their
    removed workflows are cleaned up when the active clone syncs.
    """
    remote_url = getattr(repository_record, "remote_url", None) or ""
    protected = []
    for repo in GitRepository.objects.exclude(pk=repository_record.pk).only("pk", "provided_contents", "remote_url"):
        contents = repo.provided_contents or []
        if WORKFLOW_CONTENT_IDENTIFIER not in contents:
            continue
        other_url = getattr(repo, "remote_url", None) or ""
        if remote_url and other_url and other_url == remote_url:
            # Same logical source — allow this sync to disable keys no longer on disk.
            continue
        protected.append(repo.pk)
    return protected


def _orphan_workflow_queryset(repository_record, synced_workflow_keys):
    """Return enabled workflows that should leave the launch page after this sync.

    Disables every enabled workflow whose key is absent from this sync, except workflows
    owned by a different active workflow-providing GitRepository (different remote URL).

    This catches stale FK links to replaced/duplicate GitRepository rows, NULL source_repo
    after SET_NULL, and repos that no longer provide workflow content.
    """
    qs = Workflow.objects.filter(enabled=True).exclude(key__in=synced_workflow_keys)
    protected_ids = _other_workflow_repo_ids_to_protect(repository_record)
    if protected_ids:
        qs = qs.exclude(source_repo_id__in=protected_ids)
    return qs


def _disable_workflows_missing_from_repo(repository_record, synced_workflow_keys, job_result):
    """Disable enabled workflows from this repo that were not present in the current sync.

    Workflows are disabled rather than deleted so run history is preserved. Disabled
    workflows are hidden from the launch page unless ``show_disabled`` is enabled.

    Disable is applied before optional stats/logging so a logging failure cannot leave
    orphans enabled.
    """
    strays = list(
        Workflow.objects.filter(enabled=True)
        .exclude(key__in=synced_workflow_keys)
        .only("pk", "key", "name", "source_repo_id")
    )
    if strays:
        detail = ", ".join(f"{w.key}(source_repo={w.source_repo_id or 'none'})" for w in strays)
        job_result.log(message=f"Enabled workflows not in this repository sync: {detail}")

    workflows_to_disable = list(_orphan_workflow_queryset(repository_record, synced_workflow_keys))

    job_result.log(
        message=(
            f"Checking for removed workflows: {len(workflows_to_disable)} orphan(s); "
            f"{len(synced_workflow_keys)} key(s) present in repository "
            f"({', '.join(sorted(str(k) for k in synced_workflow_keys)) or 'none'})."
        )
    )

    disabled_count = 0
    for workflow in workflows_to_disable:
        # Persist disable first — stats/reconcile must not block removal from the launch page.
        workflow.enabled = False
        workflow.save(update_fields=["enabled"])
        disabled_count += 1

        try:
            action_count = workflow.actions.count()
            input_count = workflow.inputs.count()
            run_count = WorkflowRun.objects.filter(workflow_id=workflow.pk).count()
            source = workflow.source_repo_id or "none"
            job_result.log(
                message=f"Disabling workflow '{workflow.name}' (key: {workflow.key}, source_repo={source}) "
                f"with {action_count} actions, {input_count} inputs, {run_count} runs - no longer in repository"
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


def _upsert_workflow_from_data(repository_record, data, job_result):
    """Create or update a Workflow and its inputs/actions from parsed YAML data."""
    yaml_enabled = data.get("enabled", True)
    # Prefer approval_required; accept legacy requires_approval from early drafts.
    if "approval_required" in data:
        yaml_approval_required = bool(data.get("approval_required"))
    else:
        yaml_approval_required = bool(data.get("requires_approval", False))

    wf, _ = Workflow.objects.update_or_create(
        key=data["key"],
        defaults={
            "name": data.get("name", data["key"]),
            "description": data.get("description", ""),
            "category": data.get("category", ""),
            "enabled": yaml_enabled,
            "approval_required": yaml_approval_required,
            "manual_duration_minutes": data.get("manual_duration_minutes"),
            "raw_definition": data,
            "source_repo": repository_record,
        },
    )
    reconcile_approval_definition(wf, job_result=job_result)
    wf.inputs.all().delete()
    for order, ip in enumerate(data.get("inputs", []), start=1):
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
            filter_query=ip.get("filter", {}).get("query"),
            choices=ip.get("choices"),
            columns=ip.get("columns"),
            visible_when=ip.get("show_if"),
            validators=validators,
            order=order,
        )
    # Use bulk deletion to avoid get_absolute_url issues
    WorkflowAction.objects.filter(workflow=wf).delete()

    phased_actions = [
        ("pre", _get_action_list(data, "pre_actions", "pre-actions", "before_actions", "before-actions")),
        # backwards-compatible existing key
        ("main", _get_action_list(data, "actions", "main_actions", "main-actions")),
        ("post", _get_action_list(data, "post_actions", "post-actions", "after_actions", "after-actions")),
    ]

    for phase, actions in phased_actions:
        for order, act in enumerate(actions, start=1):
            if act.get("type") == "nautobot":
                script_path = (act.get("script") or "").lstrip("/")
                if not script_path:
                    raise ValueError(f"[Workflow {wf.key}] 'script' is required for nautobot actions")

                # Verify the script exists in WorkflowHelper
                if not WorkflowHelper.objects.filter(source_repo=repository_record, file_path=script_path).exists():
                    raise ValueError(f"[Workflow {wf.key}] Script not found in repository: {script_path}")

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


def sync_workflows_from_repo(repository_record: GitRepository, job_result, delete=False):
    """Sync workflow definitions and helper files from a Git repository.

    On a normal sync (``delete=False``), workflows present in the repo are upserted and
    any previously synced workflows from this repo that are no longer present are
    disabled so they disappear from the launch page.

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
    wf_dir = get_setting("workflow_dir", fallback="workflows")
    workflows_path = repo_path / wf_dir
    if not workflows_path.exists():
        # Workflows directory removed — disable everything previously synced from this repo.
        _disable_workflows_missing_from_repo(repository_record, set(), job_result)
        clear_repo_script_cache(repository_record)
        return

    # Pass 1: discover keys from disk.
    synced_workflow_keys = set()
    workflow_docs = []
    for yml in sorted(workflows_path.glob("*.y*ml")):
        content = _process_includes(job_result, yml)
        data = yaml.safe_load(content)
        if not isinstance(data, dict) or not data.get("key"):
            job_result.log(message=f"Skipping {yml.name}: workflow YAML must be a mapping with a 'key'")
            continue
        synced_workflow_keys.add(data["key"])
        workflow_docs.append(data)

    # Pass 2: disable orphans before upsert so a later sync error cannot leave removed
    # workflows on the launch page (and so disable is not stuck behind a failing upsert).
    _disable_workflows_missing_from_repo(repository_record, synced_workflow_keys, job_result)

    _sync_repo_python_modules(repository_record, repo_path, job_result)
    validate_repo_filter_modules(repository_record)

    for data in workflow_docs:
        _upsert_workflow_from_data(repository_record, data, job_result)

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
