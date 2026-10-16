"""Signal handlers for nautobot_workflow_engine."""

import logging

logger = logging.getLogger(__name__)

# Jobs the engine needs enabled for scheduling and orphan cleanup to work.
REQUIRED_JOB_CLASS_NAMES = (
    "ScheduledWorkflowExecutorJob",
    "OrphanedWorkflowRunCleaner",
)


def enable_required_jobs(sender, *, apps, **kwargs):  # pylint: disable=unused-argument
    """Enable required workflow-engine jobs after the database is ready.

    Nautobot creates app Job rows with ``enabled=False``. Scheduled execution and
    orphan cleanup fail until those jobs are enabled. This handler turns on the
    executor and orphan-cleaner jobs on every ``nautobot_database_ready`` for this
    app. The ServiceNow sync job is left disabled for admins to enable explicitly.
    """
    Job = apps.get_model("extras", "Job")
    updated = Job.objects.filter(
        module_name="nautobot_workflow_engine.jobs",
        job_class_name__in=REQUIRED_JOB_CLASS_NAMES,
        enabled=False,
    ).update(enabled=True)
    if updated:
        logger.info("Enabled %s required Workflow Engine job(s).", updated)
