"""Every server error names the builder section it belongs to, so the nav marks the right place."""

import unittest

from nautobot_workflow_engine.schema.errors import FieldError, section_for
from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.validate import validate


class SectionForTestCase(unittest.TestCase):
    def test_pointers_map_to_sections(self):
        self.assertEqual(section_for("/actions/0/when"), "steps")
        self.assertEqual(section_for("/pre_actions/1/with/device"), "steps")
        self.assertEqual(section_for("/inputs/2/model"), "inputs")
        self.assertEqual(section_for("/schedule/servicenow/timezone"), "schedule")
        self.assertEqual(section_for("/name"), "details")
        self.assertEqual(section_for(""), "")

    def test_explicit_section_wins(self):
        self.assertEqual(FieldError("/actions/0", "x", "m", section="review")["section"], "review")

    def test_template_errors_land_on_steps(self):
        definition = {
            "key": "t",
            "name": "T",
            "actions": [{"type": "awx", "name": "run", "template_id": 1, "when": "{{ inputs.a {% if %}"}],
        }
        errors = [item for item in validate(normalize(definition)) if item["code"] == "template_syntax"]
        self.assertTrue(errors)
        self.assertEqual({item["section"] for item in errors}, {"steps"})
