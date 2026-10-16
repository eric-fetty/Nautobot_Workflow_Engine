"""Unit tests for approval-gated scheduling helpers."""

import unittest
from datetime import datetime
from datetime import timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.utils import timezone
from nautobot.extras.choices import JobExecutionType

from nautobot_workflow_engine.scheduling import (
    create_approval_gated_immediate_schedule,
    create_scheduled_job_for_run,
    scheduled_job_name_for_run,
)


class CreateScheduledJobApprovalTestCase(unittest.TestCase):
    """Validate ScheduledJob kwargs and approval-gated immediate creation."""

    def _workflow_run(self, **overrides):
        now = timezone.now()
        defaults = {
            "pk": "11111111-1111-1111-1111-111111111111",
            "workflow": SimpleNamespace(
                name="Sample Workflow",
                key="sample",
                servicenow_change_input_key=None,
            ),
            "requested_by": SimpleNamespace(username="operator"),
            "inputs": {},
            "schedule_change_ticket": "",
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

    def test_scheduled_job_name_uses_change_ticket_from_inputs(self):
        scheduled_for = datetime(2026, 10, 16, 14, 0, tzinfo=dt_timezone.utc)
        workflow_run = self._workflow_run(
            workflow=SimpleNamespace(
                name="Privileged Change",
                key="privileged-change",
                servicenow_change_input_key="change_ticket",
            ),
            requested_by=SimpleNamespace(username="eric"),
            inputs={"change_ticket": "CHG0001234"},
            scheduled_for=scheduled_for,
        )

        name = scheduled_job_name_for_run(workflow_run)

        self.assertEqual(name, "Privileged Change · eric · CHG0001234")
        self.assertNotIn(str(workflow_run.pk), name)

    def test_scheduled_job_name_prefers_schedule_change_ticket(self):
        workflow_run = self._workflow_run(
            workflow=SimpleNamespace(
                name="Privileged Change",
                key="privileged-change",
                servicenow_change_input_key="change_ticket",
            ),
            requested_by=SimpleNamespace(username="eric"),
            inputs={"change_ticket": "CHG_FROM_INPUT"},
            schedule_change_ticket="CHG0009999",
        )

        name = scheduled_job_name_for_run(workflow_run)

        self.assertEqual(name, "Privileged Change · eric · CHG0009999")
        self.assertNotIn("CHG_FROM_INPUT", name)
        self.assertNotIn(str(workflow_run.pk), name)

    def test_scheduled_job_name_falls_back_to_schedule_time(self):
        scheduled_for = datetime(2026, 10, 16, 14, 0, tzinfo=dt_timezone.utc)
        workflow_run = self._workflow_run(
            workflow=SimpleNamespace(
                name="Privileged Change",
                key="privileged-change",
                servicenow_change_input_key=None,
            ),
            requested_by=SimpleNamespace(username="eric"),
            scheduled_for=scheduled_for,
        )

        name = scheduled_job_name_for_run(workflow_run)

        self.assertEqual(name, "Privileged Change · eric · Oct 16 14:00 UTC")
        self.assertNotIn(str(workflow_run.pk), name)

    def test_create_scheduled_job_uses_composite_name(self):
        scheduled_for = datetime(2026, 10, 16, 14, 0, tzinfo=dt_timezone.utc)
        workflow_run = self._workflow_run(
            workflow=SimpleNamespace(
                name="Sample Workflow",
                key="sample",
                servicenow_change_input_key="change_ticket",
            ),
            requested_by=SimpleNamespace(username="operator"),
            inputs={"change_ticket": "CHG0001234"},
            scheduled_for=scheduled_for,
        )
        job_model = SimpleNamespace(
            class_path="nautobot_workflow_engine.jobs.ScheduledWorkflowExecutorJob",
            default_job_queue="default",
        )
        created_job = SimpleNamespace(pk="sj-3")

        with patch("nautobot_workflow_engine.scheduling.Job.objects.get", return_value=job_model), patch(
            "nautobot_workflow_engine.scheduling.ScheduledJob.objects.create", return_value=created_job
        ) as create_mock:
            create_scheduled_job_for_run(workflow_run)

        create_kwargs = create_mock.call_args.kwargs
        self.assertEqual(create_kwargs["name"], "Sample Workflow · operator · CHG0001234")
        self.assertEqual(
            create_kwargs["description"],
            "Scheduled execution of 'Sample Workflow' requested by operator",
        )
        self.assertNotIn(str(workflow_run.pk), create_kwargs["name"])
