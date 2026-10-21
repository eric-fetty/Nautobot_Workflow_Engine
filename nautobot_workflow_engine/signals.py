"""Signal handlers for nautobot_workflow_engine."""

import logging

logger = logging.getLogger(__name__)

# Jobs the engine needs enabled for scheduling, orphan cleanup, and Git sync.
REQUIRED_JOB_CLASS_NAMES = (
    "ScheduledWorkflowExecutorJob",
    "OrphanedWorkflowRunCleaner",
    "PublishWorkflowsToGit",
    "WorkflowGitMaintenance",
)
GIT_SCHEDULE_NAME = "Workflow Engine Git sync"


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
    ensure_git_maintenance_schedule(apps)


def ensure_git_maintenance_schedule(apps):
    """Create the fallback Git sync schedule once. Later runs leave an existing row alone."""
    try:
        from django.utils import timezone

        scheduled_job = apps.get_model("extras", "ScheduledJob")
        job_model = apps.get_model("extras", "Job")
    except Exception:
        logger.warning("Could not look up Job models to schedule Git maintenance.")
        return
    if scheduled_job.objects.filter(name=GIT_SCHEDULE_NAME).exists():
        return
    job = job_model.objects.filter(
        module_name="nautobot_workflow_engine.jobs",
        job_class_name="WorkflowGitMaintenance",
    ).first()
    if job is None:
        return
    minutes = 5
    try:
        from django.conf import settings

        minutes = int(settings.PLUGINS_CONFIG["nautobot_workflow_engine"].get("git_sync_interval_minutes", 5))
    except Exception:
        minutes = 5
    minutes = max(minutes, 1)
    crontab = f"*/{minutes} * * * *" if minutes < 60 else "0 * * * *"
    try:
        scheduled_job.objects.create(
            name=GIT_SCHEDULE_NAME,
            task="nautobot_workflow_engine.jobs.WorkflowGitMaintenance",
            job_model=job,
            interval="custom",
            crontab=crontab,
            start_time=timezone.now(),
            enabled=True,
            one_off=False,
            kwargs={},
            args=[],
        )
        logger.info("Scheduled Workflow Git maintenance every %s minute(s).", minutes)
    except Exception:
        logger.warning("Could not create the Workflow Git maintenance schedule.", exc_info=True)
