"""Unit tests for approval-gated scheduling helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.utils import timezone
from nautobot.extras.choices import JobExecutionType

from nautobot_workflow_engine.scheduling import (
    create_approval_gated_immediate_schedule,
    create_scheduled_job_for_run,
)


class CreateScheduledJobApprovalTestCase(unittest.TestCase):
    """Validate ScheduledJob kwargs and approval-gated immediate creation."""

    def _workflow_run(self, **overrides):
        now = timezone.now()
        defaults = {
            "pk": "11111111-1111-1111-1111-111111111111",
            "workflow": SimpleNamespace(name="Sample Workflow", key="sample"),
            "requested_by": SimpleNamespace(username="operator"),
            "scheduled_for": now,
            "scheduled_job": None,
            "status": "pending",
            "save": MagicMock(),
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_create_scheduled_job_includes_workflow_key(self):
        workflow_run = self._workflow_run()
        job_model = SimpleNamespace(
            class_path="nautobot_workflow_engine.jobs.ScheduledWorkflowExecutorJob",
            default_job_queue="default",
        )
        created_job = SimpleNamespace(pk="sj-1")

        with patch("nautobot_workflow_engine.scheduling.Job.objects.get", return_value=job_model), patch(
            "nautobot_workflow_engine.scheduling.ScheduledJob.objects.create", return_value=created_job
        ) as create_mock:
            result = create_scheduled_job_for_run(workflow_run)

        self.assertIs(result, created_job)
        create_kwargs = create_mock.call_args.kwargs
        self.assertEqual(create_kwargs["kwargs"]["workflow_key"], "sample")
        self.assertEqual(create_kwargs["kwargs"]["workflow_run_id"], str(workflow_run.pk))
        self.assertEqual(create_kwargs["interval"], JobExecutionType.TYPE_FUTURE)
        self.assertNotIn("approval_required", create_kwargs)
        self.assertEqual(workflow_run.status, "scheduled")
        workflow_run.save.assert_called()

    def test_create_approval_gated_immediate_sets_pending_approval(self):
        workflow_run = self._workflow_run(scheduled_for=None)
        created_job = SimpleNamespace(pk="sj-2")

        with patch(
            "nautobot_workflow_engine.scheduling.create_scheduled_job_for_run", return_value=created_job
        ) as create_mock:
            result = create_approval_gated_immediate_schedule(workflow_run)

        self.assertIs(result, created_job)
        self.assertIsNotNone(workflow_run.scheduled_for)
        create_mock.assert_called_once_with(workflow_run, run_status="pending_approval")
