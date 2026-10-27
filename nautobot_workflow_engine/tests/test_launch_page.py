"""The launch page: one card, fields drawn from launch_ui, and side panels."""

import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nautobot_workflow_engine.datasources import save_workflow_definition
from nautobot_workflow_engine.forms import DynamicWorkflowForm
from nautobot_workflow_engine.launch_ui import field_spec, launch_config, what_happens

User = get_user_model()


def _definition():
    return {
        "key": "launch-flow",
        "name": "Launch flow",
        "description": "Checks the launch page.",
        "category": "Lab",
        "approval_required": False,
        "inputs": [
            {"key": "reason", "label": "Reason", "type": "string", "required": True, "help_text": "Why"},
            {
                "key": "mode",
                "label": "Mode",
                "type": "choice",
                "choices": [{"value": "soft", "label": "Soft"}, {"value": "hard", "label": "Hard"}],
                "default": "soft",
            },
            {"key": "mtu", "label": "MTU", "type": "integer", "min_value": 576, "max_value": 9216, "default": 1500},
            {"key": "ports", "label": "Ports", "type": "list", "max_items": 4},
            {
                "key": "vlans",
                "label": "VLANs",
                "type": "listdict",
                "columns": [{"key": "vid", "label": "VLAN ID", "type": "integer", "required": True}],
            },
            {"key": "check_first", "label": "Check first", "type": "boolean", "default": True},
        ],
        "actions": [{"type": "awx", "name": "push", "label": "Push the change", "template_id": 3}],
    }


class LaunchPageTestCase(TestCase):
    """The page renders the new card and keeps the POST contract."""

    def setUp(self):
        self.user = User.objects.create(username="runner", is_superuser=True)
        self.client.force_login(self.user)
        self.workflow = save_workflow_definition(_definition(), source_repo=None)
        self.url = reverse("plugins:nautobot_workflow_engine:workflow_launch", kwargs={"key": self.workflow.key})

    def _config(self, response):
        match = re.search(
            r'<script id="nwe-launch-config" type="application/json">(.*?)</script>', response.content.decode(), re.S
        )
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_page_renders_card_and_panels(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Run workflow", body)
        self.assertIn("What happens", body)
        self.assertIn("Push the change", body)
        self.assertIn("launch/launch.js", body)
        # People running a workflow never see version numbers or Git state.
        page = body.split('class="nwe-ui nwe-launch"')[1].split('id="nwe-launch-toast"')[0]
        self.assertNotRegex(page, r"\bv\d+\b")
        self.assertNotIn("Git", page)
        self.assertNotIn("sync", page.lower())
        config = self._config(response)
        types = {field["key"]: field["type"] for field in config["fields"]}
        self.assertEqual(types["vlans"], "listdict")
        values = {field["key"]: field["value"] for field in config["fields"]}
        self.assertEqual(values["mode"], "soft")
        self.assertIs(values["check_first"], True)
        self.assertEqual([choice["value"] for choice in config["execution"]["choices"]], ["immediate", "scheduled"])

    def test_field_spec_describes_controls(self):
        mtu = field_spec(self.workflow.inputs.get(key="mtu"))
        self.assertIn("Between 576 and 9216", mtu["help"])
        mode = field_spec(self.workflow.inputs.get(key="mode"))
        self.assertEqual(mode["choices"], [{"value": "soft", "label": "Soft"}, {"value": "hard", "label": "Hard"}])
        reason = field_spec(self.workflow.inputs.get(key="reason"))
        self.assertTrue(reason["required"])

    def test_what_happens_lists_form_steps_and_done(self):
        rows = what_happens(self.workflow, 6)
        titles = [row["title"] for row in rows]
        self.assertEqual(titles[0], "Someone fills in the form")
        self.assertIn("Push the change", titles)
        self.assertEqual(rows[-1]["kind"], "done")

    def test_server_errors_come_back_inline(self):
        response = self.client.post(
            self.url,
            {
                "reason": "test",
                "mode": "soft",
                "mtu": "12000",
                "execution_type": "immediate",
                "vlans": "[]",
                "ports": "[]",
            },
        )
        self.assertEqual(response.status_code, 200)
        config = self._config(response)
        self.assertIn("mtu", config["errors"])
        mtu = next(field for field in config["fields"] if field["key"] == "mtu")
        self.assertEqual(str(mtu["value"]), "12000")

    def test_bound_values_are_parsed_back(self):
        form = DynamicWorkflowForm(
            self.workflow,
            data={
                "ports": json.dumps(["Ethernet1/1"]),
                "vlans": json.dumps([{"vid": 10}]),
                "execution_type": "immediate",
            },
        )
        form.is_valid()
        request = self.client.get(self.url).wsgi_request
        config = launch_config(request, self.workflow, form)
        values = {field["key"]: field["value"] for field in config["fields"]}
        self.assertEqual(values["ports"], ["Ethernet1/1"])
        self.assertEqual(values["vlans"], [{"vid": 10}])
