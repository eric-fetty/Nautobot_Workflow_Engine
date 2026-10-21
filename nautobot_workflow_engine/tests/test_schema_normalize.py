"""Normalize folds aliases and keeps unknown keys."""

import unittest

from nautobot_workflow_engine.schema.normalize import normalize


class NormalizeTestCase(unittest.TestCase):
    """Canonical form used by sync, the API, and the docs."""

    def test_phase_aliases_and_unknown_keys(self):
        canonical = normalize(
            {
                "key": "sample",
                "name": "Sample",
                "pre-actions": [{"type": "awx", "name": "before", "template_id": 1}],
                "main_actions": [{"type": "awx", "name": "main", "template_id": 2, "timeout": 5}],
                "owner": "net",
                "enabled": True,
            }
        )
        self.assertEqual(canonical["pre_actions"][0]["name"], "before")
        self.assertEqual(canonical["actions"][0]["timeout"], 5)
        self.assertEqual(canonical["owner"], "net")
        self.assertNotIn("enabled", canonical)
        self.assertNotIn("main_actions", canonical)

    def test_number_column_and_scalar_choices(self):
        canonical = normalize(
            {
                "key": "sample",
                "name": "Sample",
                "inputs": [
                    {"key": "mode", "type": "choice", "choices": ["access", {"value": "trunk"}]},
                    {
                        "key": "rows",
                        "type": "listdict",
                        "columns": [{"key": "vlan", "type": "number"}],
                    },
                ],
            }
        )
        self.assertEqual(canonical["inputs"][0]["choices"][0], {"value": "access", "label": "access"})
        self.assertEqual(canonical["inputs"][1]["columns"][0]["type"], "integer")

    def test_depends_on_accepts_many_and_a_comma_string(self):
        listed = normalize(
            {
                "key": "sample",
                "name": "Sample",
                "inputs": [
                    {"key": "target", "type": "string", "depends_on": ["site", "role"]},
                    {"key": "site", "type": "string"},
                    {"key": "role", "type": "string"},
                ],
            }
        )
        self.assertEqual(listed["inputs"][0]["depends_on"], ["site", "role"])
        comma = normalize(
            {
                "key": "sample",
                "name": "Sample",
                "inputs": [
                    {"key": "target", "type": "string", "depends_on": "site, role"},
                    {"key": "site", "type": "string"},
                    {"key": "role", "type": "string"},
                ],
            }
        )
        self.assertEqual(comma["inputs"][0]["depends_on"], ["site", "role"])

    def test_show_if_keeps_a_jinja_string_and_a_condition_object(self):
        canonical = normalize(
            {
                "key": "sample",
                "name": "Sample",
                "inputs": [
                    {"key": "note", "type": "string", "show_if": "{{ inputs.confirm }}"},
                    {"key": "other", "type": "string", "show_if": {"confirm": True}},
                    {"key": "confirm", "type": "string"},
                ],
            }
        )
        self.assertEqual(canonical["inputs"][0]["show_if"], "{{ inputs.confirm }}")
        self.assertEqual(canonical["inputs"][1]["show_if"], {"confirm": True})

    def test_false_and_zero_are_kept(self):
        canonical = normalize(
            {
                "key": "sample",
                "name": "Sample",
                "approval_required": False,
                "manual_duration_minutes": 0,
                "actions": [{"type": "awx", "name": "run", "template_id": 1, "monitor": False}],
            }
        )
        self.assertIs(canonical["approval_required"], False)
        self.assertEqual(canonical["manual_duration_minutes"], 0)
        self.assertIs(canonical["actions"][0]["monitor"], False)

    def test_type_choices_match_the_schema_enum(self):
        from nautobot_workflow_engine.models import WorkflowInput
        from nautobot_workflow_engine.schema import load_schema

        enum = load_schema()["$defs"]["input"]["properties"]["type"]["enum"]
        self.assertEqual([value for value, _label in WorkflowInput.TYPE_CHOICES], enum)
