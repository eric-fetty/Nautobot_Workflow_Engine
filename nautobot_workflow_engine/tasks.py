"""Celery tasks for AWX job monitoring and workflow execution."""

import logging
import time
import traceback

import requests
from django.utils import timezone
from nautobot.core.celery import app  # In RQ setups, adjust to RQ equivalent
from nautobot.extras.models import ExternalIntegration

from .executors import parse_awx_auth, run_pre_actions_for_scheduled_run
from .models import AwxRun, WorkflowRun
from .scheduling import create_scheduled_job_for_run
from .settings_helpers import get_setting

logger = logging.getLogger(__name__)


@app.task
def poll_awx_job(awx_run_pk: int):
    """Poll AWX job status until completion and update workflow run status."""
    poll_seconds = int(get_setting("awx_poll_seconds", 5) or 5)
    ar = AwxRun.objects.select_related("workflow_run").get(pk=awx_run_pk)
    integ = ExternalIntegration.objects.get(name=get_setting("awx_external_integration_name", "AWX"))
    url = integ.remote_url.rstrip("/")
    verify = integ.verify_ssl
    username, password = parse_awx_auth(integ)

    while True:
        r = requests.get(f"{url}/api/v2/jobs/{ar.job_id}/", auth=(username, password), verify=verify, timeout=15)
        r.raise_for_status()
        payload = r.json()
        status = payload.get("status")
        ar.status = status
        ar.raw = payload
        ar.save(update_fields=["status", "raw"])

        if status in {"successful", "failed", "error", "canceled"}:
            rr = ar.workflow_run
            # Append to existing logs instead of overwriting
            rr.log += f"\nAWX polling task: job {ar.job_id} -> {status}"

            # Log artifacts info if present for debugging
            if status == "successful" and payload.get("artifacts"):
                rr.log += f"\nAWX job artifacts captured: {list(payload['artifacts'].keys())}"
            elif status == "successful":
                rr.log += "\nAWX job completed without artifacts"

            rr.save(update_fields=["log"])
            break
        time.sleep(poll_seconds)


@app.task(bind=True)
def execute_workflow_task(self, workflow_run_id):
    """Execute a complete workflow in the background."""
    from .executors import _run_workflow_synchronous

    try:
        workflow_run = WorkflowRun.objects.get(pk=workflow_run_id)
        # Status should already be 'running', but ensure it for safety
        if workflow_run.status not in ("running", "canceled"):
            workflow_run.status = "running"
            workflow_run.save(update_fields=["status"])

        # Execute the workflow using the synchronous version
        _run_workflow_synchronous(workflow_run)

    except Exception as exc:
        try:
            workflow_run = WorkflowRun.objects.get(pk=workflow_run_id)
            workflow_run.status = "failed"
            workflow_run.log += f"\nTask execution failed: {exc}\n{traceback.format_exc()}"
            workflow_run.finished = timezone.now()
            workflow_run.save()
        except Exception:  # noqa: S110
            # If we can't even update the workflow run, just let the task fail
            pass
        raise


@app.task(bind=True)
def prepare_scheduled_workflow_task(self, workflow_run_id):
    """Run pre-actions asynchronously, then create the delayed ScheduledJob for main execution."""
    try:
        workflow_run = WorkflowRun.objects.select_related("workflow", "requested_by").get(pk=workflow_run_id)

        if workflow_run.status == "canceled":
            return

        if workflow_run.status != "running":
            workflow_run.status = "running"
            workflow_run.save(update_fields=["status"])

        if not workflow_run.scheduled_for:
            raise RuntimeError("Scheduled execution requested, but no scheduled_for timestamp was set.")

        workflow_run.log = (
            f"{workflow_run.log}\nPreparing scheduled workflow: executing pre-actions...".strip()
            if workflow_run.log
            else "Preparing scheduled workflow: executing pre-actions..."
        )
        workflow_run.save(update_fields=["log"])

        run_pre_actions_for_scheduled_run(workflow_run)
        workflow_run.refresh_from_db()

        if workflow_run.status == "canceled":
            return
        create_scheduled_job_for_run(workflow_run)
        workflow_run.refresh_from_db(fields=["scheduled_job", "status"])
        workflow_run.log = (
            f"{workflow_run.log}\nScheduled job created; execution planned for "
            f"{workflow_run.scheduled_for.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        workflow_run.save(update_fields=["log"])

    except Exception as exc:
        logger.exception("prepare_scheduled_workflow_task failed for run %s", workflow_run_id)
        try:
            workflow_run = WorkflowRun.objects.get(pk=workflow_run_id)
            if workflow_run.status != "canceled":
                workflow_run.status = "failed"
                workflow_run.finished = timezone.now()
                workflow_run.log = (
                    f"{workflow_run.log}\nScheduling aborted: {exc}\n{traceback.format_exc()}".strip()
                    if workflow_run.log
                    else f"Scheduling aborted: {exc}\n{traceback.format_exc()}"
                )
                workflow_run.save(update_fields=["status", "finished", "log", "phase_results", "user_log"])
        except Exception:  # noqa: S110
            pass
        raise
