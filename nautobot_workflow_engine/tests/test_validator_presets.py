"""Builder validation rules: the preset library, invalid patterns, and server-side checks."""

import re
import unittest
from types import SimpleNamespace

from nautobot_workflow_engine.forms import DynamicWorkflowForm
from nautobot_workflow_engine.schema.builder_payload import definition_from_builder
from nautobot_workflow_engine.schema.errors import BuilderValidationError
from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.presets import builder_presets, validator_presets
from nautobot_workflow_engine.schema.validate import validate

EXPECTED_PRESETS = {
    "hostname",
    "ipv4_address",
    "ipv4_network",
    "mac_address",
    "interface_name",
    "vlan_name",
    "servicenow_change",
}


def _base(**overrides):
    payload = {
        "key": "rules",
        "name": "Rules",
        "inputs": [],
        "actions": {"pre": [], "main": [], "post": []},
        "schedule": {"enabled": False},
    }
    payload.update(overrides)
    return payload


class ValidatorPresetTestCase(unittest.TestCase):
    """Every preset compiles in Python and sorts its own examples."""

    def test_library_has_the_agreed_presets(self):
        self.assertEqual({preset["id"] for preset in validator_presets()}, EXPECTED_PRESETS)

    def test_every_preset_accepts_and_rejects_its_examples(self):
        for preset in validator_presets():
            with self.subTest(preset=preset["id"]):
                self.assertTrue(preset["message"])
                self.assertTrue(preset["valid"])
                self.assertTrue(preset["invalid"])
                regex = re.compile(preset["regex"])
                for value in preset["valid"]:
                    self.assertIsNotNone(regex.search(value), f"{preset['id']} should accept {value!r}")
                for value in preset["invalid"]:
                    self.assertIsNone(regex.search(value), f"{preset['id']} should reject {value!r}")

    def test_presets_avoid_syntax_python_and_browsers_read_differently(self):
        # \d, \w and \s mean more in Python than in JavaScript, and named groups are spelled differently.
        for preset in validator_presets():
            with self.subTest(preset=preset["id"]):
                for token in ("\\d", "\\w", "\\s", "\\Z", "\\A", "(?P", "(?<", "(?i"):
                    self.assertNotIn(token, preset["regex"])

    def test_builder_menu_carries_only_the_rule(self):
        menu = builder_presets()
        self.assertEqual([item["id"] for item in menu], [preset["id"] for preset in validator_presets()])
        self.assertEqual(set(menu[0]), {"id", "label", "regex", "message", "flags"})

    def test_preset_rules_pass_workflow_validation(self):
        rules = [{"regex": item["regex"], "message": item["message"]} for item in builder_presets()]
        definition = definition_from_builder(
            _base(
                inputs=[
                    {"key": "host", "label": "Host", "type": "string", "validators": rules},
                    {
                        "key": "ports",
                        "label": "Ports",
                        "type": "listdict",
                        "columns": [{"key": "name", "label": "Name", "type": "string", "validators": rules}],
                    },
                ]
            )
        )
        self.assertEqual(len(definition["inputs"][0]["validators"]), len(rules))
        self.assertEqual(len(definition["inputs"][1]["columns"][0]["validators"]), len(rules))


class InvalidPatternTestCase(unittest.TestCase):
    """A pattern Python can't compile is reported against the field that holds it."""

    def test_input_pattern_error_points_at_the_rule(self):
        errors = validate(
            normalize(
                {
                    "key": "rules",
                    "name": "Rules",
                    "inputs": [
                        {"key": "host", "type": "string", "validators": [{"regex": "^ok$"}, {"regex": "^(open$"}]}
                    ],
                }
            )
        )
        self.assertEqual([error["pointer"] for error in errors], ["/inputs/0/validators/1/regex"])
        self.assertEqual(errors[0]["code"], "regex_invalid")
        self.assertEqual(errors[0]["section"], "inputs")

    def test_column_pattern_error_points_at_the_column_rule(self):
        errors = validate(
            normalize(
                {
                    "key": "rules",
                    "name": "Rules",
                    "inputs": [
                        {
                            "key": "ports",
                            "type": "listdict",
                            "columns": [
                                {"key": "vlan", "type": "integer"},
                                {"key": "name", "label": "Name", "type": "string", "validators": [{"regex": "[z-a]"}]},
                            ],
                        }
                    ],
                }
            )
        )
        self.assertEqual([error["pointer"] for error in errors], ["/inputs/0/columns/1/validators/0/regex"])
        self.assertIn("Name:", errors[0]["message"])

    def test_builder_save_rejects_an_invalid_pattern(self):
        with self.assertRaises(BuilderValidationError):
            definition_from_builder(
                _base(inputs=[{"key": "host", "label": "Host", "type": "string", "validators": [{"regex": "*bad"}]}])
            )

    def test_flags_are_applied_when_compiling(self):
        errors = validate(
            normalize(
                {
                    "key": "rules",
                    "name": "Rules",
                    "inputs": [
                        {
                            "key": "host",
                            "type": "string",
                            "validators": [{"regex": "^a b$  # spaces ignored", "flags": ["VERBOSE"]}],
                        }
                    ],
                }
            )
        )
        self.assertEqual(errors, [])

    def test_rules_without_a_pattern_are_left_out_of_the_yaml(self):
        definition = definition_from_builder(
            _base(
                inputs=[
                    {
                        "key": "host",
                        "label": "Host",
                        "type": "string",
                        "validators": [
                            {"regex": "", "message": "still typing", "flags": []},
                            {"regex": "^[a-z]+$", "message": "", "flags": []},
                        ],
                    }
                ]
            )
        )
        self.assertEqual(definition["inputs"][0]["validators"], [{"regex": "^[a-z]+$"}])


class _Inputs:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


def _list_input(validators):
    return SimpleNamespace(
        key="hosts",
        label="Hosts",
        input_type="list",
        required=True,
        default=None,
        depends_on=[],
        visible_when=None,
        validators=validators,
        content_type=None,
        columns=None,
        choices=None,
        min_items=None,
        max_items=None,
        placeholder="",
        help_text="",
    )


class ListLineValidationTestCase(unittest.TestCase):
    """The server checks each line of a list input, like the launch page does."""

    def _form(self, value):
        hostname = next(preset for preset in validator_presets() if preset["id"] == "hostname")
        workflow = SimpleNamespace(
            inputs=_Inputs([_list_input([{"regex": hostname["regex"], "message": hostname["message"]}])])
        )
        return DynamicWorkflowForm(
            workflow, data={"hosts": value, "execution_type": "immediate", "scheduled_datetime": ""}
        )

    def test_good_lines_pass(self):
        form = self._form('["leaf1", "spine-01.dc1"]')
        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_first_bad_line_is_reported(self):
        form = self._form('["leaf1", "bad host", "-x"]')
        self.assertFalse(form.is_valid())
        self.assertIn("Line 2:", form.errors.as_text())
        self.assertNotIn("Line 3:", form.errors.as_text())
