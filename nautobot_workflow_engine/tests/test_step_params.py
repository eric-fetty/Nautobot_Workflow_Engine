"""Step parameters survive the builder unchanged, and scripts describe what they ask for."""

import copy
import unittest

import yaml

from nautobot_workflow_engine.schema.builder_payload import (
    builder_from_definition,
    definition_from_builder,
    definition_to_yaml,
)
from nautobot_workflow_engine.script_params import script_parameters

DEFINITION = {
    "key": "reboot",
    "name": "Reboot",
    "inputs": [
        {"key": "device", "label": "Device", "type": "object", "model": "dcim.Device", "required": True},
        {"key": "reason", "label": "Reason", "type": "string"},
    ],
    "pre_actions": [
        {
            "type": "nautobot",
            "name": "check_reachable",
            "label": "Check the device is reachable",
            "script": "scripts/check_reachability.py",
            # One of each mode the builder infers: an input, fixed values of several types, and expressions.
            "with": {
                "device": "{{ inputs.device.id }}",
                "attempts": 3,
                "strict": True,
                "status": "active",
                "message": "Rebooted {{ inputs.device.name }} because {{ inputs.reason | default('no reason') }}",
                "options": {"retries": 2, "ports": [22, 443]},
                "zip": "0012",
            },
        }
    ],
    "actions": [
        {
            "type": "awx",
            "name": "reboot",
            "template_name": "Network · Reboot device",
            "template_id": 42,
            "extra_vars": {"target_host": "{{ inputs.device.name }}", "mode": "soft"},
            "monitor": True,
            "when": "{{ results.check_reachable.status == 'successful' }}",
        }
    ],
}


def _builder_edit(payload):
    """What the builder adds while someone edits: bookkeeping keys that must never reach YAML."""
    edited = copy.deepcopy(payload)
    for phase in edited["actions"].values():
        for action in phase:
            action["_origName"] = action["name"]
            action["_nameTouched"] = True
            action["_modes"] = {key: "expr" for key in (action.get("with") or action.get("extra_vars") or {})}
            action["_whenMode"] = "jinja"
    return edited


class StepParamRoundTripTestCase(unittest.TestCase):
    """The parameter editor only reads modes from values, so YAML written before it loads and saves unchanged."""

    def test_definition_round_trips_through_the_builder(self):
        payload = builder_from_definition(copy.deepcopy(DEFINITION))
        saved = definition_from_builder(_builder_edit(payload))
        self.assertEqual(saved["pre_actions"][0]["with"], DEFINITION["pre_actions"][0]["with"])
        self.assertEqual(saved["actions"][0]["extra_vars"], DEFINITION["actions"][0]["extra_vars"])
        self.assertEqual(saved["actions"][0]["when"], DEFINITION["actions"][0]["when"])

    def test_value_types_are_kept(self):
        saved = definition_from_builder(builder_from_definition(copy.deepcopy(DEFINITION)))
        params = saved["pre_actions"][0]["with"]
        self.assertIs(params["strict"], True)
        self.assertEqual(params["attempts"], 3)
        self.assertEqual(params["zip"], "0012")
        self.assertEqual(params["options"], {"retries": 2, "ports": [22, 443]})

    def test_yaml_has_no_builder_bookkeeping(self):
        saved = definition_from_builder(_builder_edit(builder_from_definition(copy.deepcopy(DEFINITION))))
        text = definition_to_yaml(saved)
        for marker in ("_modes", "_origName", "_nameTouched", "_whenMode"):
            self.assertNotIn(marker, text)
        loaded = yaml.safe_load(text)
        self.assertEqual(loaded["pre_actions"][0]["with"]["device"], "{{ inputs.device.id }}")

    def test_label_is_written_only_when_it_differs_from_the_key(self):
        saved = definition_from_builder(builder_from_definition(copy.deepcopy(DEFINITION)))
        self.assertEqual(saved["pre_actions"][0]["label"], "Check the device is reachable")
        self.assertEqual(saved["pre_actions"][0]["name"], "check_reachable")
        self.assertNotIn("label", saved["actions"][0])

    def test_a_step_with_only_a_name_still_loads(self):
        payload = builder_from_definition(copy.deepcopy(DEFINITION))
        self.assertEqual(payload["actions"]["main"][0]["label"], "reboot")
        self.assertEqual(payload["actions"]["main"][0]["name"], "reboot")

    def test_switching_type_keeps_label_key_and_condition(self):
        """What the step type control does: drop the script and parameters, keep the rest."""
        payload = builder_from_definition(copy.deepcopy(DEFINITION))
        step = payload["actions"]["pre"][0]
        step.update(
            {"type": "awx", "script": "", "with": {}, "template_id": 7, "template_name": "Ping", "extra_vars": {}, "monitor": True}
        )
        saved = definition_from_builder(payload)
        switched = saved["pre_actions"][0]
        self.assertEqual(switched["type"], "awx")
        self.assertEqual(switched["name"], "check_reachable")
        self.assertEqual(switched["label"], "Check the device is reachable")
        self.assertNotIn("with", switched)
        self.assertNotIn("script", switched)


class ScriptParametersTestCase(unittest.TestCase):
    """The builder lists what a script asks for without importing it."""

    def test_declared_parameters(self):
        source = (
            '"""Doc."""\n'
            "PARAMETERS = {\n"
            '    "device": {"type": "object", "model": "dcim.Device", "required": True, "help": "Device to check."},\n'
            '    "attempts": {"type": "integer", "default": 3},\n'
            "}\n"
            "def run(params):\n"
            "    return params\n"
        )
        result = script_parameters(source)
        self.assertTrue(result["declared"])
        self.assertEqual([row["name"] for row in result["params"]], ["device", "attempts"])
        self.assertEqual(result["params"][0]["model"], "dcim.Device")
        self.assertTrue(result["params"][0]["required"])
        self.assertEqual(result["params"][1]["default"], 3)
        self.assertFalse(result["params"][1]["required"])

    def test_inferred_from_params_reads(self):
        source = 'def run(params):\n    a = params["hostname"]\n    b = params.get("timeout", 5)\n    return a, b\n'
        result = script_parameters(source)
        self.assertFalse(result["declared"])
        self.assertEqual(
            [(row["name"], row["required"]) for row in result["params"]], [("hostname", True), ("timeout", False)]
        )

    def test_inferred_from_keyword_arguments(self):
        source = "def run(*, site, devices, confirm=False, **kwargs):\n    return site\n"
        result = script_parameters(source)
        self.assertEqual(
            [(row["name"], row["required"]) for row in result["params"]],
            [("site", True), ("devices", True), ("confirm", False)],
        )

    def test_code_is_never_executed(self):
        source = 'PARAMETERS = {"x": __import__("os").system("false")}\ndef run(params):\n    return params["y"]\n'
        result = script_parameters(source)
        # The dict isn't a plain literal, so it is ignored (never evaluated) and the names come from run().
        self.assertFalse(result["declared"])
        self.assertEqual([row["name"] for row in result["params"]], ["y"])

    def test_broken_source(self):
        self.assertEqual(script_parameters("def run(:"), {"declared": False, "params": []})
