"""Unit tests for workflow builder serialization."""

import unittest

import yaml

from nautobot_workflow_engine.models import integration_name_from_definition
from nautobot_workflow_engine.schema.builder_payload import (
    actions_require_repository,
    builder_from_definition,
    definition_from_builder,
    definition_to_yaml,
)
from nautobot_workflow_engine.schema.errors import BuilderValidationError


def _base(**overrides):
    payload = {
        "key": "sample-workflow",
        "name": "Sample Workflow",
        "description": "Adds an interface",
        "category": "Network",
        "enabled": True,
        "approval_required": False,
        "inputs": [],
        "actions": {"pre": [], "main": [], "post": []},
        "schedule": {"enabled": False},
    }
    payload.update(overrides)
    return payload


class BuilderSerializationTestCase(unittest.TestCase):
    """Cover every input type, both action types, and ServiceNow schedule settings."""

    def test_string_input_validators_and_show_if(self):
        definition = definition_from_builder(
            _base(
                inputs=[
                    {
                        "key": "hostname",
                        "label": "Hostname",
                        "type": "string",
                        "required": True,
                        "default": "switch-01",
                        "show_if": {"mode": "add"},
                        "validators": [
                            {"regex": "^[a-z0-9-]+$", "message": "Use a hostname.", "flags": ["IGNORECASE"]}
                        ],
                    },
                    {"key": "mode", "type": "string"},
                ]
            )
        )
        input_def = definition["inputs"][0]
        self.assertEqual(input_def["type"], "string")
        self.assertEqual(input_def["show_if"], {"mode": "add"})
        self.assertEqual(input_def["validators"][0]["flags"], ["IGNORECASE"])
        self.assertNotIn("visible_when", input_def)

    def test_integer_and_boolean_inputs(self):
        definition = definition_from_builder(
            _base(
                inputs=[
                    {"key": "vlan_id", "label": "VLAN", "type": "integer", "required": True, "default": 100},
                    {"key": "enabled", "label": "Enabled", "type": "boolean", "default": False},
                ]
            )
        )
        self.assertEqual(definition["inputs"][0]["default"], 100)
        self.assertIs(definition["inputs"][1]["default"], False)

    def test_choice_and_multichoice_inputs(self):
        definition = definition_from_builder(
            _base(
                inputs=[
                    {
                        "key": "mode",
                        "type": "choice",
                        "choices": [{"value": "access", "label": "Access"}, {"value": "trunk"}],
                    },
                    {
                        "key": "tags",
                        "type": "multichoice",
                        "choices": [{"value": "prod", "label": "Production"}],
                    },
                ]
            )
        )
        self.assertEqual(definition["inputs"][0]["choices"][1], {"value": "trunk", "label": "trunk"})
        self.assertEqual(definition["inputs"][1]["type"], "multichoice")

    def test_object_and_multiobject_inputs(self):
        definition = definition_from_builder(
            _base(
                inputs=[
                    {
                        "key": "device",
                        "type": "object",
                        "model": "dcim.Device",
                        "depends_on": ["location"],
                        "filter_query": {"location_id": "{{ inputs.location.id }}"},
                    },
                    {"key": "location", "type": "string"},
                    {"key": "devices", "type": "multiobject", "model": "dcim.Device"},
                ]
            )
        )
        self.assertEqual(definition["inputs"][0]["model"], "dcim.Device")
        self.assertEqual(definition["inputs"][0]["filter"]["query"]["location_id"], "{{ inputs.location.id }}")
        self.assertEqual(definition["inputs"][0]["depends_on"], ["location"])
        self.assertEqual(definition["inputs"][2]["type"], "multiobject")

    def test_list_input(self):
        definition = definition_from_builder(
            _base(
                inputs=[
                    {
                        "key": "dns_servers",
                        "type": "list",
                        "validators": [{"regex": r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$", "message": "IPv4"}],
                    }
                ]
            )
        )
        self.assertEqual(definition["inputs"][0]["type"], "list")
        self.assertIn("regex", definition["inputs"][0]["validators"][0])

    def test_listdict_columns(self):
        definition = definition_from_builder(
            _base(
                inputs=[
                    {
                        "key": "endpoints",
                        "type": "listdict",
                        "columns": [
                            {
                                "key": "name",
                                "label": "Name",
                                "type": "string",
                                "required": True,
                                "unique": True,
                                "validators": [{"regex": "^.+$", "message": "Required"}],
                            },
                            {
                                "key": "role",
                                "type": "choice",
                                "choices": [{"value": "leaf", "label": "Leaf"}],
                            },
                            {"key": "enabled", "type": "boolean"},
                            {"key": "vlan", "type": "integer"},
                        ],
                    }
                ]
            )
        )
        columns = definition["inputs"][0]["columns"]
        self.assertEqual([column["key"] for column in columns], ["name", "role", "enabled", "vlan"])
        self.assertTrue(columns[0]["unique"])
        self.assertEqual(columns[1]["choices"][0]["value"], "leaf")
        self.assertNotIn("required", columns[3])

    def test_legacy_regex_shorthand_becomes_validators(self):
        definition = definition_from_builder(
            builder_from_definition(
                {
                    "key": "sample-workflow",
                    "name": "Sample Workflow",
                    "inputs": [
                        {
                            "key": "hostname",
                            "type": "string",
                            "regex": "^[a-z]+$",
                            "message": "letters",
                            "flags": ["IGNORECASE"],
                        }
                    ],
                }
            )
        )
        self.assertEqual(
            definition["inputs"][0]["validators"],
            [{"regex": "^[a-z]+$", "message": "letters", "flags": ["IGNORECASE"]}],
        )
        self.assertNotIn("regex", definition["inputs"][0])

    def test_nautobot_and_awx_actions(self):
        definition = definition_from_builder(
            _base(
                actions={
                    "pre": [
                        {
                            "type": "nautobot",
                            "name": "Check",
                            "script": "/scripts/check.py",
                            "when": "{{ inputs.enabled }}",
                            "with": {"hostname": "{{ inputs.hostname }}"},
                        }
                    ],
                    "main": [
                        {
                            "type": "awx",
                            "name": "Run play",
                            "template_id": "42",
                            "extra_vars": 'site: "{{ inputs.site }}"\n',
                            "monitor": False,
                        }
                    ],
                    "post": [],
                }
            )
        )
        self.assertEqual(definition["pre_actions"][0]["script"], "scripts/check.py")
        self.assertEqual(definition["pre_actions"][0]["with"]["hostname"], "{{ inputs.hostname }}")
        self.assertEqual(definition["actions"][0]["template_id"], 42)
        self.assertIs(definition["actions"][0]["monitor"], False)
        self.assertNotIn("post_actions", definition)
        self.assertTrue(actions_require_repository(definition))

    def test_action_aliases_and_preserved_keys(self):
        canonical = definition_from_builder(
            builder_from_definition(
                {
                    "key": "sample-workflow",
                    "name": "Sample Workflow",
                    "version": 3,
                    "pre-actions": [{"type": "awx", "name": "pre", "template_id": 1}],
                    "main_actions": [{"type": "nautobot", "script": "scripts/run.py", "timeout": 5}],
                    "inputs": [{"key": "hostname", "type": "string", "description": "Device name"}],
                }
            )
        )
        self.assertEqual(canonical["version"], 3)
        self.assertEqual(canonical["pre_actions"][0]["template_id"], 1)
        self.assertNotIn("pre-actions", canonical)
        self.assertEqual(canonical["actions"][0]["timeout"], 5)
        self.assertEqual(canonical["inputs"][0]["description"], "Device name")
        self.assertEqual(definition_from_builder(builder_from_definition(canonical)), canonical)

    def test_schedule_servicenow_block(self):
        definition = definition_from_builder(
            _base(
                inputs=[{"key": "change_ticket", "type": "string", "required": True}],
                schedule={
                    "enabled": True,
                    "integration_name": "ServiceNow Lab",
                    "change_input_key": "change_ticket",
                    "allowed_states": ["scheduled", "implement"],
                    "timezone": "UTC",
                    "start_fields": ["start_date"],
                    "end_fields": ["end_date"],
                    "ticket_regex": "CHG[0-9]+",
                    "require_approved": False,
                    "preserved_servicenow": {"require_future_window": True},
                },
            )
        )
        servicenow = definition["schedule"]["servicenow"]
        self.assertEqual(servicenow["integration_name"], "ServiceNow Lab")
        self.assertEqual(servicenow["change_input_key"], "change_ticket")
        self.assertEqual(servicenow["allowed_states"], ["scheduled", "implement"])
        self.assertEqual(servicenow["timezone"], "UTC")
        self.assertEqual(servicenow["start_fields"], ["start_date"])
        self.assertEqual(servicenow["end_fields"], ["end_date"])
        self.assertEqual(servicenow["ticket_regex"], "CHG[0-9]+")
        self.assertIs(servicenow["require_approved"], False)
        self.assertIs(servicenow["require_future_window"], True)
        self.assertEqual(integration_name_from_definition(definition), "ServiceNow Lab")

    def test_missing_integration_name_defaults_to_servicenow(self):
        definition = {
            "schedule": {"servicenow": {"change_input_key": "change_ticket"}},
        }
        self.assertEqual(integration_name_from_definition(definition), "ServiceNow")
        self.assertEqual(integration_name_from_definition({}), "ServiceNow")

    def test_legacy_change_ticket_input_key(self):
        definition = definition_from_builder(
            builder_from_definition(
                {
                    "key": "sample-workflow",
                    "name": "Sample Workflow",
                    "inputs": [{"key": "change_ticket", "type": "string"}],
                    "schedule": {"change_ticket_input_key": "change_ticket"},
                }
            )
        )
        self.assertEqual(definition["schedule"]["servicenow"]["change_input_key"], "change_ticket")
        self.assertNotIn("change_ticket_input_key", definition["schedule"])

    def test_disabled_schedule_is_omitted(self):
        definition = definition_from_builder(_base())
        self.assertNotIn("schedule", definition)

    def test_yaml_round_trip(self):
        definition = definition_from_builder(
            _base(
                manual_duration_minutes=15,
                inputs=[
                    {
                        "key": "hostname",
                        "type": "string",
                        "required": True,
                        "show_if": {"confirm": ["yes", True]},
                        "validators": [{"regex": "^[a-z]+$"}],
                    },
                    {"key": "confirm", "type": "string"},
                    {
                        "key": "rows",
                        "type": "listdict",
                        "columns": [{"key": "name", "type": "string", "required": True}],
                    },
                ],
                actions={
                    "main": [
                        {
                            "type": "nautobot",
                            "name": "Run",
                            "script": "scripts/run.py",
                            "with": {"hostname": "{{ inputs.hostname }}"},
                        }
                    ]
                },
                schedule={
                    "enabled": True,
                    "integration_name": "ServiceNow",
                    "change_input_key": "hostname",
                },
            )
        )
        self.assertEqual(yaml.safe_load(definition_to_yaml(definition)), definition)
        self.assertEqual(definition_from_builder(builder_from_definition(definition)), definition)

    def test_validation_errors_collect_paths(self):
        with self.assertRaises(BuilderValidationError) as raised:
            definition_from_builder(
                {
                    "key": "Not A Slug",
                    "name": "",
                    "inputs": [
                        {"key": "device", "type": "object"},
                        {"key": "mode", "type": "choice", "choices": []},
                        {"type": "nope"},
                    ],
                    "actions": {
                        "main": [
                            {"type": "nautobot", "script": ""},
                            {"type": "awx"},
                            {"type": "other"},
                        ]
                    },
                    "schedule": {"enabled": True, "change_input_key": "missing", "timezone": "Not/AZone"},
                }
            )
        paths = {item["path"] for item in raised.exception.errors}
        self.assertIn("key", paths)
        self.assertIn("name", paths)
        self.assertIn("inputs[0].model", paths)
        self.assertIn("inputs[1].choices", paths)
        self.assertIn("inputs[2].type", paths)
        self.assertIn("actions.main[0].script", paths)
        self.assertIn("actions.main[1].template_id", paths)
        self.assertIn("schedule.change_input_key", paths)
        self.assertIn("schedule.timezone", paths)

    def test_preserved_cannot_inject_action_aliases(self):
        definition = definition_from_builder(_base(preserved={"main_actions": [{"type": "awx"}], "owner": "net"}))
        self.assertNotIn("main_actions", definition)
        self.assertEqual(definition["owner"], "net")

    def test_non_dict_preserved_is_reported(self):
        with self.assertRaises(BuilderValidationError) as raised:
            definition_from_builder(_base(preserved=["nope"]))
        self.assertIn("preserved", {item["path"] for item in raised.exception.errors})

    def test_when_must_be_a_template(self):
        with self.assertRaises(BuilderValidationError) as raised:
            definition_from_builder(
                _base(actions={"main": [{"type": "awx", "name": "run", "template_id": 1, "when": "inputs.confirm"}]})
            )
        self.assertIn("{{ inputs.confirm }}", raised.exception.errors[0]["message"])

    def test_duplicate_action_names_and_choice_default(self):
        with self.assertRaises(BuilderValidationError) as raised:
            definition_from_builder(
                _base(
                    inputs=[
                        {
                            "key": "mode",
                            "type": "choice",
                            "default": "nope",
                            "choices": [{"value": "yes", "label": "Yes"}],
                        }
                    ],
                    actions={
                        "main": [
                            {"type": "awx", "name": "run", "template_id": 1},
                            {"type": "awx", "name": "run", "template_id": 2},
                        ]
                    },
                )
            )
        paths = {item["path"] for item in raised.exception.errors}
        self.assertIn("inputs[0].default", paths)
        self.assertIn("actions.main[1].name", paths)

    def test_input_reference_cycle_is_rejected(self):
        with self.assertRaises(BuilderValidationError) as raised:
            definition_from_builder(
                _base(
                    inputs=[
                        {"key": "left", "type": "string", "depends_on": ["right"]},
                        {"key": "right", "type": "string", "depends_on": ["left"]},
                    ]
                )
            )
        self.assertIn("cycle", str(raised.exception).lower())
