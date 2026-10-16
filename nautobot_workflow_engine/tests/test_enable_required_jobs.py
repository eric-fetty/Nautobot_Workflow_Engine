"""Unit tests for auto-enabling required Workflow Engine jobs."""

import unittest
from unittest.mock import MagicMock

from nautobot_workflow_engine.signals import REQUIRED_JOB_CLASS_NAMES, enable_required_jobs


class EnableRequiredJobsTestCase(unittest.TestCase):
    """Validate enable_required_jobs updates only the required Job rows."""

    def _call_handler(self, update_return=2):
        Job = MagicMock()
        apps = MagicMock()
        apps.get_model.return_value = Job
        filter_qs = MagicMock()
        Job.objects.filter.return_value = filter_qs
        filter_qs.update.return_value = update_return

        enable_required_jobs(sender=None, apps=apps)
        return Job, apps, filter_qs

    def test_enables_executor_and_orphan_cleaner_only(self):
        Job, apps, filter_qs = self._call_handler(update_return=2)

        apps.get_model.assert_called_once_with("extras", "Job")
        Job.objects.filter.assert_called_once_with(
            module_name="nautobot_workflow_engine.jobs",
            job_class_name__in=REQUIRED_JOB_CLASS_NAMES,
            enabled=False,
        )
        filter_qs.update.assert_called_once_with(enabled=True)
        self.assertEqual(
            set(REQUIRED_JOB_CLASS_NAMES),
            {"ScheduledWorkflowExecutorJob", "OrphanedWorkflowRunCleaner"},
        )
        self.assertNotIn("SyncServiceNowScheduledWorkflowsJob", REQUIRED_JOB_CLASS_NAMES)

    def test_idempotent_when_already_enabled(self):
        _, _, filter_qs = self._call_handler(update_return=0)

        filter_qs.update.assert_called_once_with(enabled=True)
