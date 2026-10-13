"""Nautobot Jobs for the workflow engine plugin."""

from datetime import timedelta

from django.utils import timezone
from nautobot.apps.jobs import Job, StringVar, register_jobs
from nautobot.extras.models import Job as JobModel
from nautobot.extras.models import ScheduledJob

from .executors import run_workflow
from .models import WorkflowRun
from .scheduling import cancel_scheduled_run, create_scheduled_job_for_run, sync_servicenow_workflow_run
from .servicenow import ServiceNowValidationError, VALIDATION_PHASE_EXECUTION, VALIDATION_PHASE_SYNC

name = "Workflow Engine"


class OrphanedWorkflowRunCleaner(Job):
    """Clean up orphaned WorkflowRun instances."""

    class Meta:
        """Job metadata."""

        name = "Clean Orphaned Workflow Runs"
        description = "Delete WorkflowRun records that are orphaned or stuck in scheduled status"
        has_sensitive_variables = False
        hidden = True

    def run(self):
        """Identify and delete orphaned or stuck WorkflowRun instances."""
        job_model = JobModel.objects.get(
            module_name="nautobot_workflow_engine.jobs", job_class_name="ScheduledWorkflowExecutorJob"
        )
        for workflow_run in WorkflowRun.objects.filter(status__in=["scheduled", "pending"]):
            jobs = ScheduledJob.objects.filter(job_model=job_model, kwargs={"workflow_run_id": workflow_run.id})
            if not jobs:
                self.logger.info(f"Deleted orphaned WorkflowRun instance: {workflow_run.id}")
                workflow_run.delete()


class ScheduledWorkflowExecutorJob(Job):
    """Execute a pre-created WorkflowRun at scheduled time."""

    class Meta:
        """Job metadata."""

        name = "Execute Scheduled Workflow"
        description = "Execute a workflow run that was scheduled for later execution"
        has_sensitive_variables = False
        hidden = True

    workflow_run_id = StringVar(description="WorkflowRun ID to execute")

    def run(self, workflow_run_id):
        """Execute the scheduled workflow run."""
        try:
            workflow_run = WorkflowRun.objects.select_related("workflow", "requested_by").get(pk=workflow_run_id)

            if workflow_run.status == "canceled":
                self.logger.info(
                    f"WorkflowRun {workflow_run_id} was canceled before execution; skipping.",
                    extra={"grouping": "workflow-execution", "object": workflow_run},
                )
                return

            if workflow_run.status != "scheduled":
                self.logger.warning(
                    f"WorkflowRun {workflow_run_id} is in status '{workflow_run.status}', expected 'scheduled'; skipping.",
                    extra={"grouping": "workflow-execution", "object": workflow_run},
                )
                return

            if workflow_run.schedule_source == "servicenow":
                try:
                    change_window, rescheduled = sync_servicenow_workflow_run(
                        workflow_run,
                        cancel_invalid=False,
                        require_future_window=False,
                        validation_phase=VALIDATION_PHASE_EXECUTION,
                    )
                except ServiceNowValidationError as exc:
                    cancel_scheduled_run(workflow_run, f"ServiceNow validation blocked execution: {exc}")
                    self.logger.warning(
                        f"Canceled ServiceNow-backed workflow run {workflow_run_id}: {exc}",
                        extra={"grouping": "workflow-execution", "object": workflow_run},
                    )
                    return

                if change_window.window_start > timezone.now() + timedelta(seconds=30):
                    create_scheduled_job_for_run(workflow_run)
                    workflow_run.log = (
                        f"{workflow_run.log}\nExecution deferred because the ServiceNow window moved to "
                        f"{change_window.window_start.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    ).strip()
                    workflow_run.save(update_fields=["log"])
                    self.logger.info(
                        f"Deferred WorkflowRun {workflow_run_id} because the ServiceNow window moved.",
                        extra={"grouping": "workflow-execution", "object": workflow_run},
                    )
                    return

                if rescheduled:
                    self.logger.info(
                        f"Refreshed ServiceNow schedule for WorkflowRun {workflow_run_id} before execution.",
                        extra={"grouping": "workflow-execution", "object": workflow_run},
                    )

            self.logger.info(
                f"Executing scheduled workflow: {workflow_run.workflow.name}",
                extra={"grouping": "workflow-execution", "object": workflow_run},
            )

            # Update status to running
            workflow_run.started = timezone.now()
            workflow_run.status = "running"
            workflow_run.save()

            # Execute the workflow using the existing run_workflow function
            run_workflow(workflow_run.workflow, workflow_run.requested_by, workflow_run.inputs, workflow_run)

            self.logger.success(
                f"Successfully executed scheduled workflow: {workflow_run.workflow.name}",
                extra={"grouping": "workflow-execution", "object": workflow_run},
            )

        except WorkflowRun.DoesNotExist:
            error_msg = f"WorkflowRun {workflow_run_id} not found"
            self.logger.error(error_msg, extra={"grouping": "workflow-execution"})
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Error executing scheduled workflow: {e}"
            self.logger.error(error_msg, extra={"grouping": "workflow-execution"})

            # Update workflow run status to failed if we have the object
            try:
                workflow_run = WorkflowRun.objects.get(pk=workflow_run_id)
                if workflow_run.status != "canceled":
                    workflow_run.status = "failed"
                    workflow_run.log += f"\nScheduled execution failed: {e}"
                    workflow_run.save()
                    self.logger.info(
                        f"Updated WorkflowRun {workflow_run_id} status to failed",
                        extra={"grouping": "workflow-execution", "object": workflow_run},
                    )
                else:
                    self.logger.info(
                        f"WorkflowRun {workflow_run_id} is canceled; preserving canceled status.",
                        extra={"grouping": "workflow-execution", "object": workflow_run},
                    )
            except WorkflowRun.DoesNotExist:
                self.logger.warning(
                    f"Could not update WorkflowRun {workflow_run_id} status - object not found",
                    extra={"grouping": "workflow-execution"},
                )

            # Re-raise the exception to mark the job as failed
            raise


class SyncServiceNowScheduledWorkflowsJob(Job):
    """Refresh ServiceNow-backed scheduled workflow runs."""

    class Meta:
        """Job metadata."""

        name = "Sync ServiceNow Scheduled Workflows"
        description = "Revalidate scheduled workflow runs against ServiceNow change windows"
        has_sensitive_variables = False
        hidden = False

    def run(self):
        """Refresh scheduled workflow runs using their ServiceNow change tickets."""
        queryset = WorkflowRun.objects.select_related("workflow", "requested_by", "scheduled_job").filter(
            status="scheduled",
            schedule_source="servicenow",
            scheduled_job__isnull=False,
        )

        synced = 0
        rescheduled = 0
        canceled = 0

        for workflow_run in queryset:
            try:
                _change_window, was_rescheduled = sync_servicenow_workflow_run(
                    workflow_run,
                    cancel_invalid=True,
                    require_future_window=False,
                    validation_phase=VALIDATION_PHASE_SYNC,
                )
                synced += 1
                if was_rescheduled:
                    rescheduled += 1
                    self.logger.info(
                        f"Rescheduled WorkflowRun {workflow_run.pk} from ServiceNow metadata.",
                        extra={"grouping": "workflow-execution", "object": workflow_run},
                    )
            except ServiceNowValidationError as exc:
                canceled += 1
                self.logger.warning(
                    f"Canceled WorkflowRun {workflow_run.pk} during ServiceNow sync: {exc}",
                    extra={"grouping": "workflow-execution", "object": workflow_run},
                )

        self.logger.info(
            f"ServiceNow sync complete. Synced={synced}, rescheduled={rescheduled}, canceled={canceled}.",
            extra={"grouping": "workflow-execution"},
        )


register_jobs(ScheduledWorkflowExecutorJob, OrphanedWorkflowRunCleaner, SyncServiceNowScheduledWorkflowsJob)
