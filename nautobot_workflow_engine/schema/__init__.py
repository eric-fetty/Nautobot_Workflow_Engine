"""Single source for workflow definition enums, aliases, and the JSON Schema."""

import json
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "workflow.schema.json"
CANONICAL_KEY_ORDER = (
    "key",
    "name",
    "description",
    "category",
    "approval_required",
    "manual_duration_minutes",
    "schedule",
    "inputs",
    "pre_actions",
    "actions",
    "post_actions",
)
PHASES = ("pre", "main", "post")
PHASE_YAML_KEYS = {"pre": "pre_actions", "main": "actions", "post": "post_actions"}
TEMPLATE_SOURCE_LIMIT = 10 * 1024
OPERATIONAL_KEYS = frozenset({"enabled"})


@lru_cache(maxsize=1)
def load_schema():
    """Return the workflow JSON Schema document."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _property(name):
    return load_schema()["properties"][name]


def _def(name):
    return load_schema()["$defs"][name]


INPUT_TYPES = tuple(_def("input")["properties"]["type"]["enum"])
COLUMN_TYPES = tuple(_def("column")["properties"]["type"]["enum"])
ACTION_TYPES = tuple(_def("action")["properties"]["type"]["enum"])
COLUMN_TYPE_ALIASES = {
    alias: "integer" for alias in (_def("column")["properties"]["type"].get("x-aliases") or []) if alias != "number"
}


def _phase_aliases():
    """Map every YAML action-list alias onto its canonical key, including the canonical key itself."""
    found = {}
    for phase, canonical in PHASE_YAML_KEYS.items():
        found[canonical] = canonical
        for alias in _property(canonical).get("x-aliases") or []:
            found[alias] = canonical
        found[phase] = canonical
    return found


PHASE_ALIASES = _phase_aliases()


def input_type_choices():
    """Return ``(value, label)`` pairs for ``WorkflowInput.input_type``."""
    labels = _def("input")["properties"]["type"].get("x-ui", {}).get("labels") or {}
    return [(value, labels.get(value, value)) for value in INPUT_TYPES]


def action_type_choices():
    """Return ``(value, label)`` pairs for ``WorkflowAction.action_type``."""
    labels = _def("action")["properties"]["type"].get("x-ui", {}).get("labels") or {}
    return [(value, labels.get(value, value)) for value in ACTION_TYPES]


def phase_choices():
    """Return ``(value, label)`` pairs for action phases."""
    labels = {"pre": "Before", "main": "Main", "post": "Always after"}
    return [(phase, labels[phase]) for phase in PHASES]


def managed_keys():
    """Return canonical property names plus every alias."""
    keys = set(load_schema()["properties"])
    keys.update(PHASE_ALIASES)
    keys.update(OPERATIONAL_KEYS)
    keys.add("requires_approval")
    return frozenset(keys)
