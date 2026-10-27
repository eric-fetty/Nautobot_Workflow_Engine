"""Edit YAML maps errors to lines and refuses people who cannot author code."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from nautobot_workflow_engine.models import Workflow
from nautobot_workflow_engine.schema.yaml_io import pointer_lines
from nautobot_workflow_engine.tests.test_builder_views import grant

User = get_user_model()

BROKEN_TYPE = """\
key: reboot
name: Reboot
inputs:
  - key: confirm
    label: Confirm
    type: not-a-type
"""


class YamlModeTestCase(TestCase):
    """Pointer lines and the Build workflows gate on the YAML view."""

    def setUp(self):
        self.user = User.objects.create(username="yaml-author")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse("plugins-api:nautobot_workflow_engine-api:builder-yaml-apply")

    def test_pointer_line_mapping(self):
        lines, error = pointer_lines(BROKEN_TYPE)
        self.assertIsNone(error)
        self.assertEqual(lines["/inputs/0/type"], 6)

    def test_apply_returns_the_line_and_keeps_a_parsed_form(self):
        grant(self.user, ["change"], Workflow)
        response = self.client.post(
            self.url,
            data=json.dumps({"yaml": BROKEN_TYPE}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.data["payload"])
        matched = [item for item in response.data["errors"] if item.get("pointer") == "/inputs/0/type"]
        self.assertTrue(matched, response.data["errors"])
        self.assertEqual(matched[0]["line"], 6)

    def test_invalid_yaml_does_not_return_a_payload(self):
        grant(self.user, ["change"], Workflow)
        response = self.client.post(
            self.url,
            data=json.dumps({"yaml": "key: [\n"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.data["payload"])
        self.assertGreaterEqual(response.data["errors"][0]["line"], 1)
        self.assertEqual(response.data["errors"][0]["code"], "yaml")

    def test_yaml_needs_build_workflows(self):
        grant(self.user, ["view"], Workflow)
        body = json.dumps({"yaml": "key: reboot\nname: Reboot\n"})
        response = self.client.post(self.url, data=body, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Build workflows", str(response.data))
        grant(self.user, ["change"], Workflow)
        self.client.force_authenticate(User.objects.get(pk=self.user.pk))  # drop the cached permissions
        response = self.client.post(self.url, data=body, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
