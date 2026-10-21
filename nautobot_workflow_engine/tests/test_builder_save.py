"""Persistence tests for builder-created workflows."""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.extras.models import GitRepository

from nautobot_workflow_engine.builders import definition_from_builder
from nautobot_workflow_engine.datasources import save_workflow_definition
from nautobot_workflow_engine.forms import DynamicWorkflowForm
from nautobot_workflow_engine.models import Workflow, WorkflowHelper
from nautobot_workflow_engine.servicenow import integration_name_for_workflow


def _definition():
    return definition_from_builder(
        {
            "key": "sample-workflow",
            "name": "Sample Workflow",
            "inputs": [
                {
                    "key": "hostname",
                    "type": "string",
                    "required": True,
                    "show_if": {"mode": ["add", True]},
                    "validators": [{"regex": "^[a-z]+$", "message": "letters"}],
                },
                {"key": "device", "type": "object", "model": "dcim.Device"},
                {"key": "mode", "type": "string"},
            ],
            "actions": {
                "pre": [{"type": "awx", "name": "pre", "template_id": 7, "monitor": False}],
                "main": [
                    {
                        "type": "nautobot",
                        "name": "Run",
                        "script": "scripts/run.py",
                        "with": {"hostname": "{{ inputs.hostname }}"},
                    }
                ],
            },
            "schedule": {
                "enabled": True,
                "change_input_key": "hostname",
                "integration_name": "ServiceNow Lab",
            },
        }
    )


class SaveWorkflowDefinitionMappingTestCase(unittest.TestCase):
    """The shared save path writes the same rows git sync writes."""

    def _patches(self, helper_exists=True):
        created_inputs = []
        created_actions = []
        workflow = SimpleNamespace(
            key="sample-workflow",
            inputs=SimpleNamespace(all=lambda: SimpleNamespace(delete=lambda: None)),
        )
        workflow_manager = SimpleNamespace(update_or_create=MagicMock(side_effect=lambda **kwargs: (workflow, True)))
        input_manager = SimpleNamespace(create=lambda **kwargs: created_inputs.append(kwargs))
        action_manager = SimpleNamespace(
            create=lambda **kwargs: created_actions.append(kwargs),
            filter=lambda **_kwargs: SimpleNamespace(delete=lambda: None),
        )
        helper_manager = SimpleNamespace(
            filter=MagicMock(return_value=SimpleNamespace(exists=MagicMock(return_value=helper_exists)))
        )
        content_type = SimpleNamespace(id=9)
        return (
            created_inputs,
            created_actions,
            [
                patch.object(save_workflow_definition.__globals__["transaction"], "atomic", return_value=nullcontext()),
                patch("nautobot_workflow_engine.datasources.Workflow", SimpleNamespace(objects=workflow_manager)),
                patch("nautobot_workflow_engine.datasources.WorkflowInput", SimpleNamespace(objects=input_manager)),
                patch("nautobot_workflow_engine.datasources.WorkflowAction", SimpleNamespace(objects=action_manager)),
                patch("nautobot_workflow_engine.datasources.WorkflowHelper", SimpleNamespace(objects=helper_manager)),
                patch(
                    "nautobot_workflow_engine.datasources.ContentType",
                    SimpleNamespace(objects=SimpleNamespace(get=MagicMock(return_value=content_type))),
                ),
                patch("nautobot_workflow_engine.datasources.reconcile_approval_definition"),
            ],
            content_type,
        )

    def test_row_mapping_is_idempotent(self):
        definition = _definition()
        created_inputs, created_actions, patches, content_type = self._patches()
        repository = SimpleNamespace(id=1)
        for active in patches:
            active.start()
        try:
            save_workflow_definition(definition, source_repo=repository)
            first_inputs = list(created_inputs)
            first_actions = list(created_actions)
            created_inputs.clear()
            created_actions.clear()
            save_workflow_definition(definition, source_repo=repository)
        finally:
            for active in reversed(patches):
                active.stop()

        self.assertEqual(created_inputs, first_inputs)
        self.assertEqual(created_actions, first_actions)
        self.assertEqual(first_inputs[0]["visible_when"], {"mode": ["add", True]})
        self.assertEqual(first_inputs[0]["validators"][0]["regex"], "^[a-z]+$")
        self.assertEqual(first_inputs[0]["order"], 1)
        self.assertIs(first_inputs[1]["content_type"], content_type)
        self.assertEqual(first_actions[0]["phase"], "pre")
        self.assertIs(first_actions[0]["monitor"], False)
        self.assertEqual(first_actions[1]["script_path"], "/scripts/run.py")
        self.assertEqual(first_actions[1]["with_vars"]["hostname"], "{{ inputs.hostname }}")
        self.assertEqual(first_actions[1]["order"], 1)

    def test_missing_script_is_rejected(self):
        created_inputs, created_actions, patches, _content_type = self._patches(helper_exists=False)
        for active in patches:
            active.start()
        try:
            with self.assertRaises(ValueError) as raised:
                save_workflow_definition(_definition(), source_repo=SimpleNamespace(id=1))
        finally:
            for active in reversed(patches):
                active.stop()
        self.assertIn("Script not found in repository", str(raised.exception))
        self.assertEqual(created_actions, [])
        self.assertEqual(created_inputs, [])


class BuilderLaunchFormTestCase(TestCase):
    """A workflow saved from the builder is launchable without a git sync."""

    def test_dynamic_form_includes_inputs_and_servicenow_choice(self):
        repository = GitRepository.objects.create(
            name="Workflow Builder Repo",
            remote_url="https://example.com/workflow-builder.git",
        )
        WorkflowHelper.objects.create(
            source_repo=repository,
            file_path="scripts/echo.py",
            content="def run(context):\n    return {}\n",
        )
        model = ContentType.objects.get_for_model(Workflow)
        definition = definition_from_builder(
            {
                "key": "builder-sample",
                "name": "Builder Sample",
                "enabled": True,
                "inputs": [
                    {"key": "change_ticket", "label": "Change Ticket", "type": "string", "required": True},
                    {
                        "key": "device",
                        "label": "Workflow",
                        "type": "object",
                        "model": f"{model.app_label}.{Workflow.__name__}",
                    },
                    {
                        "key": "rows",
                        "label": "Rows",
                        "type": "listdict",
                        "columns": [{"key": "name", "label": "Name", "type": "string", "required": True}],
                    },
                ],
                "actions": {
                    "main": [
                        {
                            "type": "nautobot",
                            "name": "Echo",
                            "script": "scripts/echo.py",
                        }
                    ]
                },
                "schedule": {"enabled": True, "change_input_key": "change_ticket"},
            }
        )

        workflow = save_workflow_definition(definition, source_repo=repository)
        save_workflow_definition(definition, source_repo=repository)

        self.assertEqual(Workflow.objects.filter(key="builder-sample").count(), 1)
        workflow.refresh_from_db()
        self.assertEqual(workflow.inputs.count(), 3)
        self.assertEqual(workflow.actions.get().script_path, "/scripts/echo.py")
        self.assertEqual(workflow.inputs.get(key="device").content_type, model)
        self.assertEqual(workflow.servicenow_change_input_key, "change_ticket")
        self.assertEqual(integration_name_for_workflow(workflow), "ServiceNow")

        form = DynamicWorkflowForm(workflow)
        self.assertIn("change_ticket", form.fields)
        self.assertIn("device", form.fields)
        self.assertIn("rows", form.fields)
        self.assertIn("servicenow", [choice[0] for choice in form.fields["execution_type"].choices])
