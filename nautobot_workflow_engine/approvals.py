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

        from nautobot.extras.models import ApprovalWorkflowDefinition  # noqa: F401
    except ImportError:
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

    should_exist = bool(workflow.approval_required and workflow.enabled)
    if should_exist:
        return upsert_approval_definition(workflow, job_result=job_result)

    delete_approval_definition(workflow, job_result=job_result)
    return None
