"""Unit tests for approval-gated scheduling helpers."""

import unittest
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from nautobot.extras.choices import JobExecutionType
from nautobot.extras.models import ScheduledJob

from nautobot_workflow_engine.datasources import save_workflow_definition
from nautobot_workflow_engine.models import WorkflowRun
from nautobot_workflow_engine.scheduling import (
    SCHEDULED_JOB_NAME_MAX_LENGTH,
    create_approval_gated_immediate_schedule,
    create_scheduled_job_for_run,
    scheduled_job_name_for_run,
)

User = get_user_model()

NO_NAME_CLASH = {"return_value": MagicMock(exists=MagicMock(return_value=False))}


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
            "nautobot_workflow_engine.scheduling.ScheduledJob.objects.filter", **NO_NAME_CLASH
        ), patch(
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

        self.assertEqual(name, "Privileged Change · eric · CHG0001234 · run 11111111")
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

        self.assertEqual(name, "Privileged Change · eric · CHG0009999 · run 11111111")
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

        self.assertEqual(name, "Privileged Change · eric · Oct 16 14:00 UTC · run 11111111")
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
            "nautobot_workflow_engine.scheduling.ScheduledJob.objects.filter", **NO_NAME_CLASH
        ), patch(
            "nautobot_workflow_engine.scheduling.ScheduledJob.objects.create", return_value=created_job
        ) as create_mock:
            create_scheduled_job_for_run(workflow_run)

        create_kwargs = create_mock.call_args.kwargs
        self.assertEqual(create_kwargs["name"], "Sample Workflow · operator · CHG0001234 · run 11111111")
        self.assertEqual(
            create_kwargs["description"],
            "Scheduled execution of 'Sample Workflow' requested by operator",
        )
        self.assertNotIn(str(workflow_run.pk), create_kwargs["name"])

    def test_scheduled_job_name_differs_for_two_runs_with_the_same_ticket(self):
        workflow = SimpleNamespace(name="Privileged Change", key="privileged-change", servicenow_change_input_key=None)
        first = self._workflow_run(workflow=workflow, schedule_change_ticket="CHG0001234")
        second = self._workflow_run(
            pk="22222222-2222-2222-2222-222222222222", workflow=workflow, schedule_change_ticket="CHG0001234"
        )

        self.assertEqual(scheduled_job_name_for_run(first), "Privileged Change · operator · CHG0001234 · run 11111111")
        self.assertEqual(scheduled_job_name_for_run(second), "Privileged Change · operator · CHG0001234 · run 22222222")

    def test_scheduled_job_name_uses_full_run_id_when_short_name_is_taken(self):
        workflow_run = self._workflow_run(schedule_change_ticket="CHG0001234")
        clash = {"return_value": MagicMock(exists=MagicMock(return_value=True))}
        job_model = SimpleNamespace(
            class_path="nautobot_workflow_engine.jobs.ScheduledWorkflowExecutorJob",
            default_job_queue="default",
        )

        with patch("nautobot_workflow_engine.scheduling.Job.objects.get", return_value=job_model), patch(
            "nautobot_workflow_engine.scheduling.ScheduledJob.objects.filter", **clash
        ), patch(
            "nautobot_workflow_engine.scheduling.ScheduledJob.objects.create", return_value=SimpleNamespace(pk="sj-4")
        ) as create_mock:
            create_scheduled_job_for_run(workflow_run)

        self.assertEqual(
            create_mock.call_args.kwargs["name"],
            f"Sample Workflow · operator · CHG0001234 · run {workflow_run.pk}",
        )

    def test_scheduled_job_name_stays_within_the_name_limit(self):
        workflow_run = self._workflow_run(
            workflow=SimpleNamespace(name="W" * 300, key="long", servicenow_change_input_key=None),
            requested_by=SimpleNamespace(username="u" * 150),
            schedule_change_ticket="C" * 120,
        )

        name = scheduled_job_name_for_run(workflow_run, full_reference=True)

        self.assertLessEqual(len(name), SCHEDULED_JOB_NAME_MAX_LENGTH)
        self.assertTrue(name.endswith(f"run {workflow_run.pk}"))


class ScheduleTwiceWithSameTicketTestCase(TestCase):
    """Two runs of one workflow by one person with one change ticket both get a scheduled job."""

    def test_second_run_schedules(self):
        workflow = save_workflow_definition(
            {
                "key": "same-ticket-flow",
                "name": "Same ticket flow",
                "description": "",
                "category": "Lab",
                "approval_required": False,
                "inputs": [{"key": "reason", "label": "Reason", "type": "string"}],
                "actions": [{"type": "awx", "name": "push", "label": "Push", "template_id": 3}],
            },
            source_repo=None,
        )
        user = User.objects.create(username="same-ticket-user")
        later = timezone.now() + timedelta(days=1)
        runs = [
            WorkflowRun.objects.create(
                workflow=workflow,
                requested_by=user,
                status="pending",
                inputs={"reason": "x"},
                scheduled_for=later,
                schedule_change_ticket="CHG0001234",
            )
            for _ in range(2)
        ]

        jobs = [create_scheduled_job_for_run(run) for run in runs]

        self.assertNotEqual(jobs[0].name, jobs[1].name)
        for run, job in zip(runs, jobs):
            run.refresh_from_db()
            self.assertEqual(run.status, "scheduled")
            self.assertEqual(run.scheduled_job_id, job.pk)
            self.assertEqual(job.kwargs["workflow_run_id"], str(run.pk))
            self.assertTrue(job.name.startswith("Same ticket flow · same-ticket-user · CHG0001234 · run "))
        self.assertEqual(ScheduledJob.objects.filter(pk__in=[job.pk for job in jobs]).count(), 2)
