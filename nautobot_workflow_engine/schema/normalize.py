"""Turn a raw workflow mapping into the canonical definition."""

import yaml

from . import (
    ACTION_TYPES,
    CANONICAL_KEY_ORDER,
    COLUMN_TYPE_ALIASES,
    COLUMN_TYPES,
    INPUT_TYPES,
    OPERATIONAL_KEYS,
    PHASE_ALIASES,
    PHASE_YAML_KEYS,
    managed_keys,
)
from .yaml_io import order_mapping

_DROP_AFTER_FOLD = frozenset({"requires_approval", "preserved", "source_repo"}) | OPERATIONAL_KEYS


def normalize(raw):
    """Return a canonical definition.

    Aliases, the legacy approval flag, the legacy ServiceNow key, root ``regex``
    validators, scalar choices, and the ``number`` column alias are folded.
    ``enabled`` is operational and is removed. Unknown keys stay, after the schema keys.
    """
    if not isinstance(raw, dict):
        return raw
    data = dict(raw)
    _fold_phase_aliases(data)
    if "approval_required" not in data and "requires_approval" in data:
        data["approval_required"] = bool(data.pop("requires_approval"))
    for key in list(_DROP_AFTER_FOLD):
        data.pop(key, None)
    if isinstance(data.get("inputs"), list):
        data["inputs"] = [_normalize_input(item) if isinstance(item, dict) else item for item in data["inputs"]]
    for key in PHASE_YAML_KEYS.values():
        if isinstance(data.get(key), list):
            data[key] = [_normalize_action(item) if isinstance(item, dict) else item for item in data[key]]
    if "schedule" in data:
        schedule = _normalize_schedule(data.get("schedule"))
        if schedule:
            data["schedule"] = schedule
        else:
            data.pop("schedule", None)
    if isinstance(data.get("manual_duration_minutes"), str) and data["manual_duration_minutes"].isdigit():
        data["manual_duration_minutes"] = int(data["manual_duration_minutes"])
    return order_mapping(_omit_empty(data))


def _fold_phase_aliases(data):
    """Copy the first alias into the canonical key when the canonical key is absent, then drop aliases."""
    canonical_to_aliases = {}
    for alias, canonical in PHASE_ALIASES.items():
        if alias == canonical:
            continue
        canonical_to_aliases.setdefault(canonical, []).append(alias)
    for canonical, aliases in canonical_to_aliases.items():
        if canonical not in data:
            for alias in aliases:
                if alias in data:
                    data[canonical] = data[alias]
                    break
        for alias in aliases:
            if alias in data and alias != canonical:
                data.pop(alias, None)


def _normalize_input(item):
    built = dict(item)
    built.pop("preserved", None)
    built.pop("order", None)
    built.pop("filter_query", None)
    built.pop("choices_template", None)
    if "type" not in built or built.get("type") in (None, ""):
        built["type"] = "string"
    if not built.get("label") and built.get("key"):
        built["label"] = built["key"]
    validators = built.get("validators")
    if not validators and built.get("regex"):
        folded = {"regex": built.get("regex")}
        if built.get("message") not in (None, ""):
            folded["message"] = built.get("message")
        if built.get("flags"):
            folded["flags"] = built.get("flags")
        built["validators"] = [folded]
    for legacy in ("regex", "message", "flags"):
        if "validators" in built:
            built.pop(legacy, None)
    if "choices" in built:
        built["choices"] = _normalize_choices(built.get("choices"))
    if isinstance(built.get("columns"), list):
        built["columns"] = [
            _normalize_column(column) if isinstance(column, dict) else column for column in built["columns"]
        ]
    raw_filter = built.get("filter")
    if isinstance(raw_filter, dict) and "query" in raw_filter:
        query = _parse_loose(raw_filter.get("query"))
        extra = {key: value for key, value in raw_filter.items() if key != "query"}
        if query in (None, "", {}):
            built.pop("filter", None)
        else:
            merged = dict(extra)
            merged["query"] = query
            built["filter"] = merged
    if isinstance(built.get("depends_on"), str):
        built["depends_on"] = [part.strip() for part in built["depends_on"].split(",") if part.strip()]
    return _omit_empty(built)


def _normalize_column(column):
    built = dict(column)
    built.pop("preserved", None)
    column_type = built.get("type")
    if column_type in COLUMN_TYPE_ALIASES:
        built["type"] = COLUMN_TYPE_ALIASES[column_type]
    if "choices" in built:
        built["choices"] = _normalize_choices(built.get("choices"))
    return _omit_empty(built)


def _normalize_choices(choices):
    if isinstance(choices, str) or choices in (None, ""):
        return choices
    if not isinstance(choices, list):
        return choices
    normalized = []
    for choice in choices:
        if isinstance(choice, dict):
            item = dict(choice)
            if "value" in item and item.get("label") in (None, ""):
                item["label"] = str(item.get("value"))
            normalized.append(_omit_empty(item))
        elif isinstance(choice, (str, int, float)) and not isinstance(choice, bool):
            normalized.append({"value": choice, "label": str(choice)})
        else:
            normalized.append(choice)
    return normalized


def _normalize_action(item):
    built = dict(item)
    preserved = built.pop("preserved", None)
    built.pop("order", None)
    built.pop("phase", None)
    if isinstance(preserved, dict):
        for key, value in preserved.items():
            built.setdefault(key, value)
    action_type = built.get("type")
    if not built.get("name") and action_type in ACTION_TYPES:
        built["name"] = action_type
    if isinstance(built.get("script"), str):
        built["script"] = built["script"].strip().lstrip("/")
    if isinstance(built.get("template_id"), str) and built["template_id"].isdigit():
        built["template_id"] = int(built["template_id"])
    for field in ("with", "extra_vars"):
        if field in built:
            built[field] = _parse_loose(built.get(field))
    return _omit_empty(built)


def _normalize_schedule(schedule):
    if not isinstance(schedule, dict) or not schedule:
        return None
    data = dict(schedule)
    preserved = data.pop("preserved_schedule", None)
    preserved_snow = data.pop("preserved_servicenow", None)
    flat_keys = (
        "integration_name",
        "change_input_key",
        "allowed_states",
        "timezone",
        "start_fields",
        "end_fields",
        "ticket_regex",
        "require_approved",
    )
    servicenow = dict(data.get("servicenow")) if isinstance(data.get("servicenow"), dict) else {}
    for key in flat_keys:
        if key in data and key not in servicenow:
            servicenow[key] = data.pop(key)
    data.pop("enabled", None)
    legacy = data.pop("change_ticket_input_key", None)
    if legacy and "change_input_key" not in servicenow:
        servicenow["change_input_key"] = legacy
    if isinstance(preserved_snow, dict):
        for key, value in preserved_snow.items():
            servicenow.setdefault(key, value)
    servicenow = _omit_empty(servicenow)
    if servicenow:
        data["servicenow"] = servicenow
    if isinstance(preserved, dict):
        for key, value in preserved.items():
            if key not in data and key not in flat_keys and key != "servicenow":
                data[key] = value
    return _omit_empty(data) or None


def _parse_loose(value):
    """Parse a YAML string when it loads as a mapping. Leave other strings alone."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    if text[0] not in "{[\n" and ":" not in text:
        return value
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return value
    return loaded


def _omit_empty(data):
    """Drop None and empty strings, lists, and dicts. Keep false and zero."""
    if not isinstance(data, dict):
        return data
    cleaned = {}
    for key, value in data.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        cleaned[key] = value
    ordered = {}
    for key in CANONICAL_KEY_ORDER:
        if key in cleaned:
            ordered[key] = cleaned[key]
    for key, value in cleaned.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def colliding_unknown_keys(raw):
    """Return unknown top-level keys that are aliases of managed keys."""
    if not isinstance(raw, dict):
        return []
    managed = managed_keys()
    collisions = []
    preserved = raw.get("preserved") if isinstance(raw.get("preserved"), dict) else {}
    for source in (raw, preserved):
        for key in source:
            if key in ("preserved", "source_repo"):
                continue
            canonical = PHASE_ALIASES.get(key)
            if key in managed and key not in CANONICAL_KEY_ORDER and canonical:
                collisions.append(key)
            elif key in PHASE_ALIASES and key != PHASE_ALIASES[key]:
                collisions.append(key)
    return collisions


def known_input_types():
    """Expose the schema enum for callers that cannot import the constant twice."""
    return INPUT_TYPES


def known_column_types():
    """Column types after alias folding."""
    return COLUMN_TYPES
