"""Run Again only prefills the launch form from a run the person may view."""

import json
import re
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from nautobot.users.models import ObjectPermission

from nautobot_workflow_engine.datasources import save_workflow_definition
from nautobot_workflow_engine.default_permissions import RUN_WORKFLOWS
from nautobot_workflow_engine.models import WorkflowRun

User = get_user_model()
VIEW_RUNS = "Workflow Engine: View runs"
SECRET = "secret-from-previous-run"


def _definition():
    return {
        "key": "run-again-flow",
        "name": "Run again flow",
        "description": "",
        "category": "Lab",
        "approval_required": False,
        "inputs": [
            {"key": "reason", "label": "Reason", "type": "string", "required": True},
            {
                "key": "mode",
                "label": "Mode",
                "type": "choice",
                "choices": [{"value": "soft", "label": "Soft"}, {"value": "hard", "label": "Hard"}],
                "default": "soft",
            },
        ],
        "actions": [{"type": "awx", "name": "push", "label": "Push", "template_id": 3}],
    }


class RunAgainPrefillTestCase(TestCase):
    """Launch-form pre-fill from ?from_run= follows view-run permission."""

    def setUp(self):
        self.workflow = save_workflow_definition(_definition(), source_repo=None)
        self.workflow.enabled = True
        self.workflow.save()
        self.owner = User.objects.create(username="run-owner")
        self.run = WorkflowRun.objects.create(
            workflow=self.workflow,
            requested_by=self.owner,
            status="success",
            inputs={"reason": SECRET, "mode": "hard"},
        )
        self.url = reverse("plugins:nautobot_workflow_engine:workflow_launch", kwargs={"key": self.workflow.key})

    def _login(self, username, *permission_names):
        user = User.objects.create(username=username)
        for name in permission_names:
            ObjectPermission.objects.get(name=name).users.add(user)
        user = User.objects.get(pk=user.pk)
        self.client.force_login(user)
        return user

    def _values(self, response):
        match = re.search(
            r'<script id="nwe-launch-config" type="application/json">(.*?)</script>',
            response.content.decode(),
            re.S,
        )
        self.assertIsNotNone(match)
        config = json.loads(match.group(1))
        return {field["key"]: field["value"] for field in config["fields"]}

    def test_permitted_user_gets_prefill(self):
        self._login("viewer-runner", RUN_WORKFLOWS, VIEW_RUNS)
        response = self.client.get(self.url, {"from_run": self.run.pk})
        self.assertEqual(response.status_code, 200)
        values = self._values(response)
        self.assertEqual(values["reason"], SECRET)
        self.assertEqual(values["mode"], "hard")

    def test_unpermitted_user_does_not_get_prefill(self):
        user = self._login("runner-only", RUN_WORKFLOWS)
        self.assertFalse(user.has_perm("nautobot_workflow_engine.view_workflowrun", self.run))
        response = self.client.get(self.url, {"from_run": self.run.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SECRET, response.content.decode())
        values = self._values(response)
        self.assertNotEqual(values.get("reason"), SECRET)
        self.assertIn(values.get("reason"), (None, ""))
        # The choice field keeps its workflow default rather than the hidden run's value.
        self.assertEqual(values.get("mode"), "soft")

    def test_bad_id_is_ignored(self):
        self._login("viewer-runner", RUN_WORKFLOWS, VIEW_RUNS)
        missing = uuid.uuid4()
        for bad in ("not-a-uuid", "99", str(missing)):
            response = self.client.get(self.url, {"from_run": bad})
            self.assertEqual(response.status_code, 200, msg=bad)
            self.assertNotIn(SECRET, response.content.decode())
            values = self._values(response)
            self.assertIn(values.get("reason"), (None, ""))
