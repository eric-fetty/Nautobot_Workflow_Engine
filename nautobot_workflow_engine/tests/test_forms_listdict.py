"""Focused form tests for listdict workflows."""

import unittest
from types import SimpleNamespace

from nautobot_workflow_engine.forms import DynamicWorkflowForm


class _FakeInputs:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


def _workflow_input(**overrides):
    defaults = {
        "key": "devices",
        "label": "Devices",
        "input_type": "listdict",
        "required": True,
        "default": None,
        "depends_on": [],
        "visible_when": None,
        "validators": None,
        "content_type": None,
        "columns": [
            {"key": "hostname", "label": "Hostname", "type": "string", "required": True, "unique": True},
            {
                "key": "vlan",
                "label": "VLAN",
                "type": "integer",
                "required": True,
            },
            {
                "key": "role",
                "label": "Role",
                "type": "choice",
                "choices": [{"value": "leaf", "label": "Leaf"}, {"value": "spine", "label": "Spine"}],
            },
        ],
        "choices": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _workflow(*inputs):
    return SimpleNamespace(inputs=_FakeInputs(inputs))


class DynamicWorkflowFormListdictTestCase(unittest.TestCase):
    """Verify listdict fields parse and validate imported tabular data."""

    def test_listdict_field_uses_listdict_enhancer(self):
        workflow = _workflow(_workflow_input())

        form = DynamicWorkflowForm(workflow)

        self.assertIn("devices", form.fields)
        self.assertEqual(form.fields["devices"].widget.attrs["data-enhance"], "list-dict")

    def test_listdict_payload_is_coerced_and_validated_server_side(self):
        workflow = _workflow(_workflow_input())
        form = DynamicWorkflowForm(
            workflow,
            data={
                "devices": '[{"hostname": "leaf-01", "vlan": "101", "role": "leaf"}]',
                "execution_type": "immediate",
                "scheduled_datetime": "",
            },
        )

        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data["devices"][0]["hostname"], "leaf-01")
        self.assertEqual(form.cleaned_data["devices"][0]["vlan"], 101)

    def test_listdict_payload_rejects_duplicate_and_invalid_rows(self):
        workflow = _workflow(_workflow_input())
        form = DynamicWorkflowForm(
            workflow,
            data={
                "devices": (
                    '[{"hostname": "leaf-01", "vlan": "101", "role": "leaf"}, '
                    '{"hostname": "leaf-01", "vlan": "abc", "role": "bad"}]'
                ),
                "execution_type": "immediate",
                "scheduled_datetime": "",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Row 2, Hostname: value must be unique", form.errors.as_text())
        self.assertIn("Row 2, VLAN: VLAN must be a number.", form.errors.as_text())
        self.assertIn("Row 2, Role: invalid choice 'bad'.", form.errors.as_text())

    def test_listdict_choice_column_accepts_display_label(self):
        workflow = _workflow(_workflow_input())
        form = DynamicWorkflowForm(
            workflow,
            data={
                "devices": '[{"hostname": "leaf-01", "vlan": "101", "role": "Leaf"}]',
                "execution_type": "immediate",
                "scheduled_datetime": "",
            },
        )

        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data["devices"][0]["role"], "leaf")
