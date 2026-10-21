"""Launch-form preview and the rebuilt builder page."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from nautobot_workflow_engine.models import Workflow
from nautobot_workflow_engine.schema import load_schema
from nautobot_workflow_engine.tests.test_builder_views import grant

User = get_user_model()


class LaunchPreviewTestCase(TestCase):
    """The preview is HTML, and errors stay out of the YAML pane."""

    def setUp(self):
        self.user = User.objects.create(username="previewer")
        grant(self.user, ["add", "view", "change"], Workflow)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_preview_renders_labels_and_help(self):
        response = self.client.post(
            reverse("plugins-api:nautobot_workflow_engine-api:builder-launch-preview"),
            data=json.dumps(
                {
                    "definition": {
                        "key": "reboot-device",
                        "name": "Reboot device",
                        "inputs": [
                            {
                                "key": "reason",
                                "label": "Reason for reboot",
                                "type": "string",
                                "required": True,
                                "help_text": "Why this device is rebooting",
                            }
                        ],
                    }
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.content.decode()
        self.assertIn("Reason for reboot", body)
        self.assertIn("Why this device is rebooting", body)
        self.assertNotIn("<script", body.lower())

    def test_builder_page_has_the_studio_shell(self):
        grant(self.user, ["add"], Workflow)
        self.client.force_login(self.user)
        response = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_builder_create"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn("Save draft", body)
        self.assertIn("studio-stepper", body)
        self.assertIn("workflow_builder.css", body)
        self.assertIn("builder/studio.js", body)
        self.assertNotIn("<style>", body)
        self.assertNotIn("Managed in Git", body)
        self.assertEqual(
            load_schema()["properties"]["schedule"]["x-ui"]["label"],
            "ServiceNow change window",
        )

    def test_validate_keeps_yaml_beside_errors(self):
        response = self.client.post(
            reverse("plugins-api:nautobot_workflow_engine-api:builder-validate"),
            data=json.dumps(
                {
                    "strict": False,
                    "payload": {
                        "key": "",
                        "name": "Reboot device",
                        "inputs": [{"key": "reason", "label": "Reason", "type": "string", "show_if": "{{ inputs."}],
                        "actions": {"pre": [], "main": [], "post": []},
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsInstance(response.data["yaml"], str)
        self.assertIn("name:", response.data["yaml"])
        self.assertTrue(response.data["errors"])
        self.assertNotIn(response.data["errors"][0]["message"], response.data["yaml"])
