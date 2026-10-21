"""Test run renders conditions and parameters and does not execute anything."""

import json
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from nautobot.extras.models import ObjectChange, ScheduledJob
from rest_framework.test import APIClient

from nautobot_workflow_engine.dry_run import dry_run
from nautobot_workflow_engine.forms import DynamicWorkflowForm
from nautobot_workflow_engine.models import Workflow, WorkflowInput, WorkflowRun
from nautobot_workflow_engine.tests.test_builder_views import grant

User = get_user_model()

DEFINITION = {
    "key": "reboot-device",
    "name": "Reboot device",
    "inputs": [
        {"key": "confirm", "label": "Confirm", "type": "boolean", "required": True},
        {"key": "device", "label": "Device", "type": "string", "help_text": "Which device", "placeholder": "edge-01"},
    ],
    "actions": [
        {
            "type": "awx",
            "name": "reboot",
            "when": "{{ inputs.confirm }}",
            "template_id": 42,
            "extra_vars": {"device": "{{ inputs.device }}"},
        }
    ],
}


class DryRunTestCase(TestCase):
    """Rendering, skips, strict undefined, and the promise of no side effects."""

    def setUp(self):
        self.user = User.objects.create(username="dry-runner")
        grant(self.user, ["view", "add", "change", "author_code"], Workflow)

    def test_false_condition_skips_and_true_renders_parameters(self):
        skipped = dry_run(DEFINITION, {"confirm": False, "device": "edge-01"}, {}, self.user)
        step = skipped["phases"][1]["steps"][0]
        self.assertFalse(step["will_run"])
        self.assertEqual(step["when_rendered"], "False")
        self.assertIn("only true/1/yes/on/y run", step["skip_reason"])

        ran = dry_run(DEFINITION, {"confirm": True, "device": "edge-01"}, {}, self.user)
        step = ran["phases"][1]["steps"][0]
        self.assertTrue(step["will_run"])
        self.assertEqual(step["params"]["device"], "edge-01")
        self.assertEqual(ran["form_errors"], [])

    def test_strict_undefined_is_an_error(self):
        definition = json.loads(json.dumps(DEFINITION))
        definition["actions"][0]["when"] = "{{ inputs.missing }}"
        result = dry_run(definition, {"confirm": True, "device": "edge-01"}, {}, self.user)
        step = result["phases"][1]["steps"][0]
        self.assertFalse(step["will_run"])
        lowered = step["error"].lower()
        self.assertTrue("undefined" in lowered or "no attribute" in lowered, step["error"])

    def test_dry_run_does_not_execute(self):
        runs = WorkflowRun.objects.count()
        jobs = ScheduledJob.objects.count()
        changes = ObjectChange.objects.count()
        with patch("nautobot_workflow_engine.executors.run_workflow") as execute:
            dry_run(DEFINITION, {"confirm": True, "device": "edge-01"}, {}, self.user)
        execute.assert_not_called()
        self.assertEqual(WorkflowRun.objects.count(), runs)
        self.assertEqual(ScheduledJob.objects.count(), jobs)
        self.assertEqual(ObjectChange.objects.count(), changes)

    def test_timeout_and_output_cap(self):
        def slow(*_args, **_kwargs):
            time.sleep(0.4)
            return []

        with patch("nautobot_workflow_engine.dry_run.render_phases", slow):
            stopped = dry_run(DEFINITION, {"confirm": True, "device": "edge-01"}, {}, self.user, timeout=0.05)
        self.assertIn("stopped", stopped["error"])
        self.assertEqual(stopped["phases"], [])

        huge = json.loads(json.dumps(DEFINITION))
        huge["actions"][0]["when"] = "{{ 'x' * 80 }}"
        capped = dry_run(huge, {"confirm": True, "device": "edge-01"}, {}, self.user, max_output=16)
        self.assertIn("1 MB", capped["phases"][1]["steps"][0]["error"])

    def test_api_accepts_a_definition_and_a_saved_workflow(self):
        client = APIClient()
        client.force_authenticate(self.user)
        preview = client.post(
            reverse("plugins-api:nautobot_workflow_engine-api:builder-dry-run"),
            data=json.dumps({"definition": DEFINITION, "inputs": {"confirm": False, "device": "edge-01"}}),
            content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertFalse(preview.data["phases"][1]["steps"][0]["will_run"])

        workflow = Workflow.objects.create(key="reboot-device", name="Reboot device", raw_definition=DEFINITION)
        saved = client.post(
            reverse("plugins-api:nautobot_workflow_engine-api:workflow-dry-run", kwargs={"key": workflow.key}),
            data=json.dumps({"inputs": {"confirm": True, "device": "edge-01"}}),
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertTrue(saved.data["phases"][1]["steps"][0]["will_run"])

    def test_help_text_and_placeholder_reach_the_launch_form(self):
        workflow = Workflow.objects.create(key="help-device", name="Help device", raw_definition={})
        WorkflowInput.objects.create(
            workflow=workflow,
            key="reason",
            label="Reason",
            input_type="string",
            help_text="Why this device is rebooting",
            placeholder="power supply",
        )
        form = DynamicWorkflowForm(workflow)
        field = form.fields["reason"]
        self.assertEqual(field.help_text, "Why this device is rebooting")
        self.assertEqual(field.widget.attrs["placeholder"], "power supply")
