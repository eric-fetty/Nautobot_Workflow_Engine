"""Git repository sync functionality for workflows."""

import re
from pathlib import Path

import yaml
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.datasources import DatasourceContent
from nautobot.extras.models import GitRepository

from .approvals import reconcile_approval_definition
from .executors import clear_repo_script_cache, validate_repo_filter_modules
from .models import Workflow, WorkflowAction, WorkflowFilterModule, WorkflowHelper, WorkflowInput

PLUGIN_CFG = settings.PLUGINS_CONFIG.get("nautobot_workflow_engine", {})


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
    filters_dir = Path(PLUGIN_CFG.get("filters_dir", "filters"))
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


def sync_workflows_from_repo(repository_record: GitRepository, job_result, delete=False):
    """Sync workflow definitions and helper files from a Git repository."""
    repo_path = Path(repository_record.filesystem_path)
    wf_dir = PLUGIN_CFG["workflow_dir"]
    workflows_path = repo_path / wf_dir
    if not workflows_path.exists():
        clear_repo_script_cache(repository_record)
        return

    # Track workflows found in this sync
    synced_workflow_keys = set()

    _sync_repo_python_modules(repository_record, repo_path, job_result)
    validate_repo_filter_modules(repository_record)

    for yml in sorted(list(workflows_path.glob("*.y*ml"))):
        content = _process_includes(job_result, yml)
        data = yaml.safe_load(content)

        # Track this workflow key
        workflow_key = data.get("key")
        if workflow_key:
            synced_workflow_keys.add(workflow_key)

        # Check if workflow is explicitly disabled in YAML
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
                "enabled": yaml_enabled,  # Use enabled flag from YAML
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
                    raise ValueError(
                        f"[Workflow {wf.key}] Unsupported action type in phase '{phase}': {act.get('type')}"
                    )

    # Disable workflows that are no longer in the repository (instead of deleting)
    if delete:
        workflows_to_disable = Workflow.objects.filter(
            source_repo=repository_record,
            enabled=True,  # Only disable currently enabled workflows
        ).exclude(key__in=synced_workflow_keys)

        disabled_count = 0

        for workflow in workflows_to_disable:
            # Count related objects for logging
            action_count = workflow.actions.count()
            input_count = workflow.inputs.count()
            run_count = workflow.runs.count()

            job_result.log(
                message=f"Disabling workflow '{workflow.name}' (key: {workflow.key}) "
                f"with {action_count} actions, {input_count} inputs, {run_count} runs - no longer in repository"
            )

            # Disable instead of delete to preserve history
            workflow.enabled = False
            workflow.save(update_fields=["enabled"])
            reconcile_approval_definition(workflow, job_result=job_result)
            disabled_count += 1

        if disabled_count > 0:
            job_result.log(
                message=f"Disabled {disabled_count} workflows no longer present in repository. Run history preserved."
            )

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
