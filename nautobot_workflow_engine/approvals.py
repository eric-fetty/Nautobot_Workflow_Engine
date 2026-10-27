"""Helpers for optional Nautobot 3.2+ ApprovalWorkflowDefinition integration."""

from __future__ import annotations

import logging
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db.models import Max
from packaging.version import Version

logger = logging.getLogger(__name__)

APPROVAL_DEFINITION_NAME_PREFIX = "Workflow Engine: "
MINIMUM_NAUTOBOT_VERSION = Version("3.2.0")


def nautobot_supports_approval_workflows() -> bool:
    """Return True when the installed Nautobot version supports approval workflows for this feature."""
    try:
        from importlib.metadata import version

        from nautobot.extras import models as extras_models
    except ImportError:
        return False

    if not hasattr(extras_models, "ApprovalWorkflowDefinition"):
        return False

    try:
        return Version(version("nautobot")) >= MINIMUM_NAUTOBOT_VERSION
    except Exception:  # noqa: BLE001
        return False


def approval_definition_name(workflow) -> str:
    """Return the stable ApprovalWorkflowDefinition name for a workflow."""
    return f"{APPROVAL_DEFINITION_NAME_PREFIX}{workflow.key}"


def approval_constraints(workflow) -> dict:
    """Return ScheduledJob constraints scoped to a specific workflow key."""
    return {
        "job_model__job_class_name": "ScheduledWorkflowExecutorJob",
        "kwargs__workflow_key": workflow.key,
    }


def _log(job_result, message: str, level: str = "info") -> None:
    """Log to a JobResult when provided, otherwise to module logger."""
    if job_result is not None and hasattr(job_result, "log"):
        job_result.log(message=message)
        return
    getattr(logger, level, logger.info)(message)


def _next_weight(model_content_type) -> int:
    """Allocate a unique weight for ApprovalWorkflowDefinition on the given content type."""
    from nautobot.extras.models import ApprovalWorkflowDefinition

    current_max = ApprovalWorkflowDefinition.objects.filter(model_content_type=model_content_type).aggregate(
        Max("weight")
    )["weight__max"]
    if current_max is None:
        return 1
    return current_max + 1


def _get_approval_definition(name: str):
    """Return an ApprovalWorkflowDefinition by name, or None."""
    from nautobot.extras.models import ApprovalWorkflowDefinition

    return ApprovalWorkflowDefinition.objects.filter(name=name).first()


def upsert_approval_definition(workflow, job_result=None):
    """Create or update the scoped ApprovalWorkflowDefinition shell for a workflow.

    Stages are intentionally not created; operators configure approver groups in the Nautobot UI.
    """
    from nautobot.extras.models import ApprovalWorkflowDefinition, ScheduledJob

    name = approval_definition_name(workflow)
    constraints = approval_constraints(workflow)
    content_type = ContentType.objects.get_for_model(ScheduledJob)

    existing = _get_approval_definition(name)
    if existing:
        update_fields = []
        if existing.model_constraints != constraints:
            existing.model_constraints = constraints
            update_fields.append("model_constraints")
        if existing.model_content_type_id != content_type.id:
            existing.model_content_type = content_type
            update_fields.append("model_content_type")
        if update_fields:
            existing.save(update_fields=update_fields)
            _log(job_result, f"Updated approval workflow definition '{name}' for workflow '{workflow.key}'.")
        else:
            _log(job_result, f"Approval workflow definition '{name}' already up to date.")
        return existing

    definition = ApprovalWorkflowDefinition.objects.create(
        name=name,
        model_content_type=content_type,
        model_constraints=constraints,
        weight=_next_weight(content_type),
    )
    _log(
        job_result,
        f"Created approval workflow definition '{name}' for workflow '{workflow.key}'. "
        "Add at least one approval stage in the Nautobot UI before runs can complete approval.",
    )
    return definition


class ApprovalRemovalError(ValueError):
    """Raised when approval cannot be turned off because runs are still waiting."""

    path = "approval_required"


def _is_approval_definition(definition) -> bool:
    """True for a real definition. MagicMock stand-ins used in unit tests are ignored."""
    return definition is not None and type(definition).__name__ == "ApprovalWorkflowDefinition"


def approval_stage_summary(workflow) -> list:
    """Return the stages configured on this workflow's approval definition."""
    if workflow is None or not nautobot_supports_approval_workflows():
        return []
    definition = _get_approval_definition(approval_definition_name(workflow))
    if not _is_approval_definition(definition):
        return []
    stages = definition.approval_workflow_stage_definitions.all().order_by("sequence")
    summary = []
    for stage in stages:
        group = getattr(stage, "approver_group", None)
        summary.append(
            {
                "sequence": stage.sequence,
                "name": stage.name,
                "min_approvers": stage.min_approvers,
                "approver_group_id": stage.approver_group_id,
                "approver_group_name": getattr(group, "name", "") or "",
                "denial_message": stage.denial_message or "",
            }
        )
    return summary


def pending_approval_count(workflow) -> int:
    """Return how many approval workflows for this definition are still pending."""
    if workflow is None or not nautobot_supports_approval_workflows():
        return 0
    definition = _get_approval_definition(approval_definition_name(workflow))
    return _pending_count(definition)


def _pending_count(definition) -> int:
    """Count pending approval workflows attached to a definition."""
    if not _is_approval_definition(definition):
        return 0
    try:
        from nautobot.extras.choices import ApprovalWorkflowStateChoices

        pending = ApprovalWorkflowStateChoices.PENDING
    except ImportError:
        pending = "pending"
    related = getattr(definition, "approval_workflows", None)
    if related is None:
        return 0
    try:
        return related.filter(current_state=pending).count()
    except Exception:  # noqa: BLE001
        return 0


def _snapshot_stages(workflow, definition) -> None:
    """Store the current stages on the workflow before the definition is removed."""
    if not _is_approval_definition(definition):
        return
    related = getattr(definition, "approval_workflow_stage_definitions", None)
    if related is None:
        return
    snapshot = []
    for stage in related.all().order_by("sequence"):
        group = getattr(stage, "approver_group", None)
        snapshot.append(
            {
                "sequence": stage.sequence,
                "name": stage.name,
                "min_approvers": stage.min_approvers,
                "approver_group_id": stage.approver_group_id,
                "approver_group_name": getattr(group, "name", "") or "",
                "denial_message": getattr(stage, "denial_message", "") or "",
            }
        )
    workflow.approval_stage_snapshot = snapshot
    workflow.save(update_fields=["approval_stage_snapshot"])


def _restore_stages(workflow, definition, job_result=None) -> None:
    """Recreate stages from the snapshot. Skip a stage whose group is gone."""
    snapshot = workflow.approval_stage_snapshot or []
    if not snapshot or not _is_approval_definition(definition):
        return
    from django.contrib.auth.models import Group
    from nautobot.extras.models import ApprovalWorkflowStageDefinition

    skipped = []
    for stage in snapshot:
        group = None
        group_id = stage.get("approver_group_id")
        if group_id:
            group = Group.objects.filter(pk=group_id).first()
        if group is None and stage.get("approver_group_name"):
            group = Group.objects.filter(name=stage["approver_group_name"]).first()
        if group is None:
            skipped.append(stage.get("name") or "stage")
            continue
        ApprovalWorkflowStageDefinition.objects.update_or_create(
            approval_workflow_definition=definition,
            sequence=stage.get("sequence") or 1,
            defaults={
                "name": stage.get("name") or "Stage",
                "min_approvers": stage.get("min_approvers") or 1,
                "denial_message": stage.get("denial_message") or "",
                "approver_group": group,
            },
        )
    workflow.approval_stage_snapshot = None
    workflow.save(update_fields=["approval_stage_snapshot"])
    if skipped:
        _log(
            job_result,
            f"Skipped approval stages whose groups no longer exist: {', '.join(skipped)}.",
            level="warning",
        )


def delete_approval_definition(workflow, job_result=None) -> bool:
    """Delete the scoped ApprovalWorkflowDefinition when present.

    Returns True when deleted (or already absent), False when deletion failed.
    """
    name = approval_definition_name(workflow)
    existing = _get_approval_definition(name)
    if not existing:
        return True

    try:
        existing.delete()
        _log(job_result, f"Deleted approval workflow definition '{name}' for workflow '{workflow.key}'.")
        return True
    except Exception as exc:  # noqa: BLE001
        _log(
            job_result,
            f"Unable to delete approval workflow definition '{name}' for workflow '{workflow.key}': {exc}. "
            "Resolve any pending approval workflows, then re-sync or delete the definition manually.",
            level="warning",
        )
        logger.warning("Failed deleting ApprovalWorkflowDefinition %s: %s", name, exc)
        return False


def reconcile_approval_definition(workflow, job_result=None) -> Optional[object]:
    """Create or delete the ApprovalWorkflowDefinition based on workflow flags.

    Returns the definition when created/updated, None when deleted or skipped.
    """
    if not nautobot_supports_approval_workflows():
        _log(
            job_result,
            f"Skipping approval definition reconcile for '{workflow.key}': requires Nautobot "
            f"{MINIMUM_NAUTOBOT_VERSION}+ with ApprovalWorkflowDefinition support.",
        )
        return None

    # Disabling a workflow must not delete stages an admin already configured.
    should_exist = bool(workflow.approval_required)
    if should_exist:
        definition = upsert_approval_definition(workflow, job_result=job_result)
        if definition is not None and getattr(workflow, "approval_stage_snapshot", None):
            _restore_stages(workflow, definition, job_result=job_result)
        return definition

    name = approval_definition_name(workflow)
    existing = _get_approval_definition(name)
    pending = _pending_count(existing)
    if pending:
        raise ApprovalRemovalError(f"{pending} runs are waiting for approval. Resolve them first.")
    if existing is not None:
        _snapshot_stages(workflow, existing)
    delete_approval_definition(workflow, job_result=job_result)
    return None
