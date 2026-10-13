"""Focused unit tests for ServiceNow-backed scheduling."""

from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from django.utils import timezone

from nautobot_workflow_engine.forms import DynamicWorkflowForm
from nautobot_workflow_engine.jobs import ScheduledWorkflowExecutorJob
from nautobot_workflow_engine.scheduling import sync_servicenow_workflow_run
from nautobot_workflow_engine.servicenow import (
    ServiceNowValidationError,
    VALIDATION_PHASE_EXECUTION,
    VALIDATION_PHASE_SYNC,
    get_change_window,
)


class _FakeInputs:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


def _workflow_input(**overrides):
    defaults = {
        "key": "change_ticket",
        "label": "Change Ticket",
        "input_type": "string",
        "required": True,
        "default": None,
        "depends_on": [],
        "visible_when": None,
        "validators": None,
        "content_type": None,
        "columns": None,
        "choices": None,
        "filter_query": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _workflow(*inputs, **overrides):
    defaults = {
        "inputs": _FakeInputs(inputs),
        "servicenow_change_input_key": "change_ticket",
        "servicenow_schedule_config": {},
        "pk": 1,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class DynamicWorkflowFormServiceNowTestCase(unittest.TestCase):
    """Validate launch-form handling for the ServiceNow execution mode."""

    def test_servicenow_execution_choice_is_rendered_when_configured(self):
        form = DynamicWorkflowForm(_workflow(_workflow_input()))

        choices = [value for value, _label in form.fields["execution_type"].choices]
        self.assertIn("servicenow", choices)

    def test_servicenow_execution_normalizes_change_ticket(self):
        form = DynamicWorkflowForm(
            _workflow(_workflow_input()),
            data={
                "change_ticket": " chg_12345 ",
                "execution_type": "servicenow",
                "scheduled_datetime": "",
            },
        )

        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data["change_ticket"], "CHG_12345")

    def test_servicenow_execution_rejects_bad_ticket_format(self):
        form = DynamicWorkflowForm(
            _workflow(_workflow_input()),
            data={
                "change_ticket": "bad ticket",
                "execution_type": "servicenow",
                "scheduled_datetime": "",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Enter a valid ServiceNow change ticket", form.errors.as_text())


class ServiceNowClientTestCase(unittest.TestCase):
    """Validate normalized change-window parsing."""

    @patch("nautobot_workflow_engine.servicenow._parse_auth", return_value=("user", "pass"))
    @patch("nautobot_workflow_engine.servicenow._get_servicenow_integration")
    @patch("nautobot_workflow_engine.servicenow.requests.get")
    def test_get_change_window_returns_normalized_result(self, mock_get, mock_integration, _mock_auth):
        mock_integration.return_value = SimpleNamespace(remote_url="https://snow.example", verify_ssl=True, name="SNOW")
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "result": [
                    {
                        "number": {"value": "CHG0001234", "display_value": "CHG0001234"},
                        "sys_id": {"value": "sys-1", "display_value": "sys-1"},
                        "state": {"value": 0.0, "display_value": "Review"},
                        "approval": {"value": "approved", "display_value": "Approved"},
                        "start_date": {
                            "value": "2030-06-05 12:00:00",
                            "display_value": "2030-06-05 05:00:00",
                            "display_value_internal": "2030-06-05 05:00:00",
                        },
                        "end_date": {
                            "value": "2030-06-05 13:00:00",
                            "display_value": "2030-06-05 06:00:00",
                            "display_value_internal": "2030-06-05 06:00:00",
                        },
                    },
                    {"__meta": {"encodedQuery": "number=CHG0001234"}},
                ]
            },
        )

        change_window = get_change_window("chg0001234", _workflow())

        self.assertEqual(change_window.change_number, "CHG0001234")
        self.assertEqual(change_window.sys_id, "sys-1")
        self.assertEqual(change_window.state_display, "Review")
        self.assertEqual(change_window.window_start.tzinfo, dt_timezone.utc)
        self.assertEqual(change_window.window_start.hour, 12)
        self.assertLess(change_window.window_start, change_window.window_end)

    @patch("nautobot_workflow_engine.servicenow._parse_auth", return_value=("user", "pass"))
    @patch("nautobot_workflow_engine.servicenow._get_servicenow_integration")
    @patch("nautobot_workflow_engine.servicenow.requests.get")
    def test_get_change_window_rejects_missing_schedule_fields(self, mock_get, mock_integration, _mock_auth):
        mock_integration.return_value = SimpleNamespace(remote_url="https://snow.example", verify_ssl=True, name="SNOW")
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "result": [
                    {
                        "number": {"value": "CHG0001234", "display_value": "CHG0001234"},
                        "sys_id": {"value": "sys-1", "display_value": "sys-1"},
                        "state": {"value": "scheduled", "display_value": "Scheduled"},
                    }
                ]
            },
        )

        with self.assertRaises(ServiceNowValidationError):
            get_change_window("CHG0001234", _workflow())

    @patch("nautobot_workflow_engine.servicenow._parse_auth", return_value=("user", "pass"))
    @patch("nautobot_workflow_engine.servicenow._get_servicenow_integration")
    @patch("nautobot_workflow_engine.servicenow.requests.get")
    def test_get_change_window_allows_unapproved_change_during_launch(self, mock_get, mock_integration, _mock_auth):
        mock_integration.return_value = SimpleNamespace(remote_url="https://snow.example", verify_ssl=True, name="SNOW")
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "result": [
                    {
                        "number": {"value": "CHG0001234", "display_value": "CHG0001234"},
                        "sys_id": {"value": "sys-1", "display_value": "sys-1"},
                        "state": {"value": 0.0, "display_value": "Review"},
                        "approval": {"value": "requested", "display_value": "Requested"},
                        "start_date": {
                            "value": "2030-06-05 12:00:00",
                            "display_value": "2030-06-05 05:00:00",
                        },
                        "end_date": {
                            "value": "2030-06-05 13:00:00",
                            "display_value": "2030-06-05 06:00:00",
                        },
                    }
                ]
            },
        )

        change_window = get_change_window("CHG0001234", _workflow())

        self.assertEqual(change_window.change_number, "CHG0001234")

    @patch("nautobot_workflow_engine.servicenow._parse_auth", return_value=("user", "pass"))
    @patch("nautobot_workflow_engine.servicenow._get_servicenow_integration")
    @patch("nautobot_workflow_engine.servicenow.requests.get")
    def test_get_change_window_rejects_unapproved_change_during_execution(self, mock_get, mock_integration, _mock_auth):
        mock_integration.return_value = SimpleNamespace(remote_url="https://snow.example", verify_ssl=True, name="SNOW")
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "result": [
                    {
                        "number": {"value": "CHG0001234", "display_value": "CHG0001234"},
                        "sys_id": {"value": "sys-1", "display_value": "sys-1"},
                        "state": {"value": 0.0, "display_value": "Review"},
                        "approval": {"value": "requested", "display_value": "Requested"},
                        "start_date": {
                            "value": "2030-06-05 12:00:00",
                            "display_value": "2030-06-05 05:00:00",
                        },
                        "end_date": {
                            "value": "2030-06-05 13:00:00",
                            "display_value": "2030-06-05 06:00:00",
                        },
                    }
                ]
            },
        )

        with self.assertRaises(ServiceNowValidationError):
            get_change_window("CHG0001234", _workflow(), validation_phase=VALIDATION_PHASE_EXECUTION)


class ServiceNowSchedulingHelperTestCase(unittest.TestCase):
    """Validate synchronization behavior for ServiceNow-backed runs."""

    @patch("nautobot_workflow_engine.scheduling.timezone.now")
    @patch("nautobot_workflow_engine.scheduling.get_change_window")
    def test_sync_servicenow_workflow_run_reschedules_run_and_job(self, mock_get_change_window, mock_now):
        now = datetime(2030, 6, 5, 11, 0, tzinfo=dt_timezone.utc)
        new_start = now + timedelta(hours=2)
        new_end = new_start + timedelta(hours=1)
        mock_now.return_value = now
        mock_get_change_window.return_value = SimpleNamespace(
            change_number="CHG0001234",
            sys_id="sys-1",
            window_start=new_start,
            window_end=new_end,
        )

        scheduled_job = SimpleNamespace(start_time=now + timedelta(hours=1), time_zone="US/Eastern", save=Mock())
        workflow_run = SimpleNamespace(
            workflow=_workflow(),
            inputs={"change_ticket": "CHG0001234"},
            schedule_source="servicenow",
            schedule_change_ticket="CHG0001234",
            schedule_external_system="",
            schedule_external_record_id="",
            schedule_window_start=None,
            schedule_window_end=None,
            schedule_last_synced=None,
            schedule_sync_error="old error",
            scheduled_for=now + timedelta(hours=1),
            scheduled_job=scheduled_job,
            save=Mock(),
        )

        change_window, rescheduled = sync_servicenow_workflow_run(workflow_run)

        self.assertTrue(rescheduled)
        self.assertEqual(change_window.change_number, "CHG0001234")
        self.assertEqual(workflow_run.scheduled_for, new_start)
        self.assertEqual(workflow_run.schedule_window_end, new_end)
        self.assertEqual(workflow_run.schedule_sync_error, "")
        scheduled_job.save.assert_called_once()
        self.assertEqual(mock_get_change_window.call_args.kwargs["validation_phase"], VALIDATION_PHASE_SYNC)


class ScheduledWorkflowExecutorJobTestCase(unittest.TestCase):
    """Validate stricter ServiceNow checks just before execution."""

    @patch("nautobot_workflow_engine.jobs.run_workflow")
    @patch("nautobot_workflow_engine.jobs.sync_servicenow_workflow_run")
    @patch("nautobot_workflow_engine.jobs.WorkflowRun.objects.select_related")
    def test_executor_uses_execution_validation_phase(self, mock_select_related, mock_sync, mock_run_workflow):
        workflow_run = SimpleNamespace(
            pk="123",
            status="scheduled",
            schedule_source="servicenow",
            workflow=SimpleNamespace(name="Demo Workflow"),
            requested_by=SimpleNamespace(),
            inputs={},
            started=None,
            save=Mock(),
        )
        mock_select_related.return_value.get.return_value = workflow_run
        mock_sync.return_value = (
            SimpleNamespace(window_start=timezone.now(), window_end=timezone.now() + timedelta(hours=1)),
            False,
        )

        ScheduledWorkflowExecutorJob().run(workflow_run_id="123")

        self.assertEqual(mock_sync.call_args.kwargs["validation_phase"], VALIDATION_PHASE_EXECUTION)
        mock_run_workflow.assert_called_once()
