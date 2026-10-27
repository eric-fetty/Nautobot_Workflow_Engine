"""Scheduling helpers shared across views, tasks, and jobs."""

import logging
from datetime import timedelta

from django.utils import timezone
from nautobot.extras.choices import JobExecutionType
from nautobot.extras.models import Job, ScheduledJob

from .servicenow import (
    VALIDATION_PHASE_SYNC,
    ServiceNowValidationError,
    get_change_window,
    integration_name_for_workflow,
)

logger = logging.getLogger(__name__)


def _append_log(existing, message):
    """Append a line to an existing log body."""
    return f"{existing}\n{message}".strip() if existing else message


def _change_ticket_for_run(workflow_run):
    """Resolve a ServiceNow change ticket for display on a scheduled job title.

    Prefers ``schedule_change_ticket`` (normalized at launch/sync). Otherwise reads
    the workflow input named by ``schedule.servicenow.change_input_key``.
    """
    stored = (getattr(workflow_run, "schedule_change_ticket", None) or "").strip()
    if stored:
        return stored

    change_input_key = getattr(workflow_run.workflow, "servicenow_change_input_key", None)
    if not change_input_key:
        return ""
    raw = (workflow_run.inputs or {}).get(change_input_key)
    if raw is None:
        return ""
    return str(raw).strip()


def _requester_username(workflow_run):
    """Return the requesting user's username, or a safe fallback."""
    requested_by = getattr(workflow_run, "requested_by", None)
    username = getattr(requested_by, "username", None)
    return username.strip() if isinstance(username, str) and username.strip() else "unknown"


def scheduled_job_name_for_run(workflow_run):
    """Build a compact ScheduledJob name for approval widgets (no run UUID).

    Format: ``{workflow name} · {requester} · {change ticket or schedule time}``.
    """
    workflow_name = getattr(workflow_run.workflow, "name", None) or "Workflow"
    username = _requester_username(workflow_run)
    ticket = _change_ticket_for_run(workflow_run)
    if ticket:
        third = ticket
    elif workflow_run.scheduled_for:
        third = workflow_run.scheduled_for.strftime("%b %d %H:%M UTC")
    else:
        third = "unscheduled"
    return f"{workflow_name} · {username} · {third}"


def disable_and_delete_scheduled_job(scheduled_job):
    """Best-effort cleanup for a ScheduledJob associated with a workflow run."""
    if not scheduled_job:
        return

    try:
        scheduled_job.enabled = False
        scheduled_job.save(update_fields=["enabled"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to disable scheduled job %s: %s", getattr(scheduled_job, "pk", "?"), exc)

    try:
        scheduled_job.delete()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to delete scheduled job %s: %s", getattr(scheduled_job, "pk", "?"), exc)


def create_scheduled_job_for_run(workflow_run, *, run_status="scheduled"):
    """Create a hidden one-off ScheduledJob for a prepared workflow run.

    Args:
        workflow_run: WorkflowRun that already has scheduled_for set.
        run_status: Status to set on the WorkflowRun after the ScheduledJob is created
            (``scheduled`` for deferred runs, ``pending_approval`` for approval-gated immediate).
    """
    if not workflow_run.scheduled_for:
        raise RuntimeError("Scheduled execution requested, but no scheduled_for timestamp was set.")

    try:
        job_model = Job.objects.get(
            module_name="nautobot_workflow_engine.jobs", job_class_name="ScheduledWorkflowExecutorJob"
        )
    except Job.DoesNotExist as exc:
        raise RuntimeError("ScheduledWorkflowExecutorJob not found in Job registry") from exc

    username = _requester_username(workflow_run)
    # Include workflow_key so ApprovalWorkflowDefinition model_constraints can scope per workflow.
    # Do not set approval_required here; Nautobot begins an approval workflow on create when a
    # matching ApprovalWorkflowDefinition exists.
    scheduled_job_kwargs = {
        "name": scheduled_job_name_for_run(workflow_run),
        "task": job_model.class_path,
        "job_model": job_model,
        "job_queue": job_model.default_job_queue,
        "kwargs": {
            "workflow_run_id": str(workflow_run.pk),
            "workflow_key": workflow_run.workflow.key,
        },
        "start_time": workflow_run.scheduled_for,
        "one_off": True,
        "user": workflow_run.requested_by,
        "interval": JobExecutionType.TYPE_FUTURE,
        "args": [],
        "crontab": "",
        "celery_kwargs": {
            "nautobot_job_ignore_singleton_lock": False,
            "nautobot_job_profile": False,
            "queue": "default",
        },
        "time_zone": "UTC",
        "description": f"Scheduled execution of '{workflow_run.workflow.name}' requested by {username}",
        "enabled": True,
        "total_run_count": 0,
    }
    allowed_fields = {field.name for field in ScheduledJob._meta.fields}
    filtered_kwargs = {k: v for k, v in scheduled_job_kwargs.items() if k in allowed_fields}
    scheduled_job = ScheduledJob.objects.create(**filtered_kwargs)

    workflow_run.scheduled_job = scheduled_job
    workflow_run.status = run_status
    workflow_run.save(update_fields=["scheduled_job", "status"])
    return scheduled_job


def create_approval_gated_immediate_schedule(workflow_run):
    """Create a near-term ScheduledJob so an immediate launch enters the approval pipeline."""
    now = timezone.now()
    if not workflow_run.scheduled_for:
        workflow_run.scheduled_for = now
        workflow_run.save(update_fields=["scheduled_for"])

    return create_scheduled_job_for_run(workflow_run, run_status="pending_approval")


def cancel_scheduled_run(workflow_run, reason):
    """Cancel a scheduled workflow run and remove its ScheduledJob."""
    scheduled_job = workflow_run.scheduled_job
    canceled_at = timezone.now()
    workflow_run.status = "canceled"
    workflow_run.finished = canceled_at
    workflow_run.scheduled_for = None
    workflow_run.scheduled_job = None
    workflow_run.schedule_sync_error = reason
    workflow_run.log = _append_log(workflow_run.log, reason)
    workflow_run.user_log = _append_log(workflow_run.user_log, reason)
    workflow_run.save(
        update_fields=[
            "status",
            "finished",
            "scheduled_for",
            "scheduled_job",
            "schedule_sync_error",
            "log",
            "user_log",
        ]
    )
    disable_and_delete_scheduled_job(scheduled_job)


def sync_servicenow_workflow_run(
    workflow_run,
    *,
    cancel_invalid=True,
    require_future_window=False,
    validation_phase=VALIDATION_PHASE_SYNC,
):
    """Refresh a ServiceNow-backed scheduled workflow run from its change ticket."""
    now = timezone.now()
    change_ticket = _change_ticket_for_run(workflow_run)

    try:
        integration_name = integration_name_for_workflow(workflow_run.workflow)
        change_window = get_change_window(
            change_ticket=change_ticket,
            workflow=workflow_run.workflow,
            integration_name=integration_name,
            require_future_window=require_future_window,
            validation_phase=validation_phase,
        )
    except ServiceNowValidationError as exc:
        workflow_run.schedule_last_synced = now
        workflow_run.schedule_sync_error = str(exc)
        workflow_run.save(update_fields=["schedule_last_synced", "schedule_sync_error"])
        if cancel_invalid:
            cancel_scheduled_run(workflow_run, f"ServiceNow synchronization canceled this workflow: {exc}")
        raise

    if change_window.window_end <= now:
        exc = ServiceNowValidationError("ServiceNow change window has already ended.")
        workflow_run.schedule_last_synced = now
        workflow_run.schedule_sync_error = str(exc)
        workflow_run.save(update_fields=["schedule_last_synced", "schedule_sync_error"])
        if cancel_invalid:
            cancel_scheduled_run(workflow_run, f"ServiceNow synchronization canceled this workflow: {exc}")
        raise exc

    update_fields = ["schedule_last_synced", "schedule_sync_error"]
    workflow_run.schedule_last_synced = now
    workflow_run.schedule_sync_error = ""

    field_updates = {
        "schedule_external_system": integration_name,
        "schedule_change_ticket": change_window.change_number,
        "schedule_external_record_id": change_window.sys_id,
        "schedule_window_start": change_window.window_start,
        "schedule_window_end": change_window.window_end,
    }
    for field_name, new_value in field_updates.items():
        if getattr(workflow_run, field_name) != new_value:
            setattr(workflow_run, field_name, new_value)
            update_fields.append(field_name)

    rescheduled = False
    buffer_cutoff = now + timedelta(seconds=30)
    if change_window.window_start > buffer_cutoff and workflow_run.scheduled_for != change_window.window_start:
        workflow_run.scheduled_for = change_window.window_start
        update_fields.append("scheduled_for")
        rescheduled = True

    workflow_run.save(update_fields=list(dict.fromkeys(update_fields)))

    if rescheduled and workflow_run.scheduled_job:
        scheduled_job_fields = []
        if getattr(workflow_run.scheduled_job, "start_time", None) != workflow_run.scheduled_for:
            workflow_run.scheduled_job.start_time = workflow_run.scheduled_for
            scheduled_job_fields.append("start_time")
        if (
            hasattr(workflow_run.scheduled_job, "time_zone")
            and getattr(workflow_run.scheduled_job, "time_zone", None) != "UTC"
        ):
            workflow_run.scheduled_job.time_zone = "UTC"
            scheduled_job_fields.append("time_zone")
        if scheduled_job_fields:
            workflow_run.scheduled_job.save(update_fields=scheduled_job_fields)

    return change_window, rescheduled
