"""Unit tests for approval-gated immediate launch behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponseRedirect
from django.test import RequestFactory

from nautobot_workflow_engine.views import WorkflowRunCreateView


class ApprovalGatedLaunchTestCase(unittest.TestCase):
    """Validate immediate launches for approval-required workflows."""

    def setUp(self):
        self.factory = RequestFactory()
        self.view = WorkflowRunCreateView.as_view()
        self.user = SimpleNamespace(
            username="operator",
            is_authenticated=True,
            is_superuser=True,
            pk=1,
            has_perms=lambda _perms: True,
        )

    def _request(self, data):
        request = self.factory.post("/plugins/workflow-engine/workflows/sample/launch/", data)
        request.user = self.user
        # Messages middleware is required by the view
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)
        return request

    def test_approval_required_immediate_uses_schedule_not_celery(self):
        workflow = SimpleNamespace(
            key="sample",
            name="Sample",
            approval_required=True,
            servicenow_change_input_key=None,
        )
        form = MagicMock()
        form.is_valid.return_value = True
        form.cleaned_data = {"execution_type": "immediate", "hostname": "sw1"}

        created_run = MagicMock()
        created_run.pk = "run-1"
        created_run.log = ""
        created_run.status = "pending"
        created_run.scheduled_for = None
        created_run.save = MagicMock()

        qs = MagicMock()
        qs.get.return_value = workflow  # get_object_or_404 uses qs

        request = self._request(
            {
                "execution_type": "immediate",
                "form_duration_seconds": "1",
            }
        )

        with patch("nautobot_workflow_engine.views._get_workflow_queryset", return_value=qs), patch(
            "nautobot_workflow_engine.views.get_object_or_404", return_value=workflow
        ), patch("nautobot_workflow_engine.views.DynamicWorkflowForm", return_value=form), patch(
            "nautobot_workflow_engine.views.WorkflowRun.objects.create", return_value=created_run
        ), patch("nautobot_workflow_engine.views.nautobot_supports_approval_workflows", return_value=True), patch(
            "nautobot_workflow_engine.views.create_approval_gated_immediate_schedule"
        ) as schedule_mock, patch("nautobot_workflow_engine.views.run_workflow") as run_workflow_mock, patch(
            "nautobot_workflow_engine.views.redirect", return_value=HttpResponseRedirect("/run/run-1/")
        ):
            response = self.view(request, key="sample")

        self.assertEqual(response.status_code, 302)
        schedule_mock.assert_called_once_with(created_run)
        run_workflow_mock.assert_not_called()
        self.assertEqual(created_run.status, "pending_approval")

    def test_non_approval_immediate_uses_celery(self):
        workflow = SimpleNamespace(
            key="sample",
            name="Sample",
            approval_required=False,
            servicenow_change_input_key=None,
        )
        form = MagicMock()
        form.is_valid.return_value = True
        form.cleaned_data = {"execution_type": "immediate", "hostname": "sw1"}

        created_run = MagicMock()
        created_run.pk = "run-2"
        created_run.log = ""
        created_run.status = "pending"
        created_run.save = MagicMock()

        request = self._request({"execution_type": "immediate", "form_duration_seconds": "1"})

        with patch("nautobot_workflow_engine.views._get_workflow_queryset", return_value=MagicMock()), patch(
            "nautobot_workflow_engine.views.get_object_or_404", return_value=workflow
        ), patch("nautobot_workflow_engine.views.DynamicWorkflowForm", return_value=form), patch(
            "nautobot_workflow_engine.views.WorkflowRun.objects.create", return_value=created_run
        ), patch("nautobot_workflow_engine.views.nautobot_supports_approval_workflows", return_value=True), patch(
            "nautobot_workflow_engine.views.create_approval_gated_immediate_schedule"
        ) as schedule_mock, patch("nautobot_workflow_engine.views.run_workflow") as run_workflow_mock, patch(
            "nautobot_workflow_engine.views.redirect", return_value=HttpResponseRedirect("/run/run-2/")
        ):
            response = self.view(request, key="sample")

        self.assertEqual(response.status_code, 302)
        schedule_mock.assert_not_called()
        run_workflow_mock.assert_called_once()
        self.assertEqual(created_run.status, "running")
