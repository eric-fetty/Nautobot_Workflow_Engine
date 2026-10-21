"""Convert the current builder page payload to and from a canonical definition.

The page still edits this payload until the M4 UI. Saving it runs the same
normalize and validate pipeline as Git sync and the API.
"""

import yaml

from . import PHASE_YAML_KEYS, PHASES, managed_keys
from .errors import BuilderValidationError, FieldError
from .normalize import normalize
from .validate import reserved_key_errors, validate
from .yaml_io import to_yaml

_INPUT_MANAGED = {
    "key",
    "label",
    "type",
    "required",
    "default",
    "depends_on",
    "show_if",
    "validators",
    "regex",
    "message",
    "flags",
    "choices",
    "model",
    "filter",
    "filter_query",
    "columns",
    "order",
    "preserved",
    "help_text",
    "placeholder",
    "choices_template",
}
_COLUMN_MANAGED = {"key", "label", "type", "required", "unique", "choices", "validators", "preserved"}
_ACTION_MANAGED = {
    "type",
    "name",
    "when",
    "script",
    "with",
    "template_id",
    "extra_vars",
    "monitor",
    "order",
    "phase",
    "preserved",
}
_SNOW_MANAGED = {
    "integration_name",
    "change_input_key",
    "allowed_states",
    "timezone",
    "start_fields",
    "end_fields",
    "ticket_regex",
    "require_approved",
}


def definition_from_builder(payload, *, enforce_policy=False, repo=None, check_scripts=False):
    """Validate a builder payload and return the canonical definition."""
    raw, errors = payload_to_raw(payload)
    if errors:
        raise BuilderValidationError(errors)
    if not isinstance(raw, dict):
        raise BuilderValidationError([FieldError("", "invalid_type", "Builder payload must be an object.")])
    errors.extend(reserved_key_errors(payload if isinstance(payload, dict) else raw))
    canonical = normalize(raw)
    errors.extend(validate(canonical, repo=repo, enforce_policy=enforce_policy, check_scripts=check_scripts))
    if errors:
        raise BuilderValidationError(errors)
    return canonical


def definition_to_yaml(definition):
    """Dump a canonical definition to YAML text."""
    return to_yaml(definition)


def empty_builder_payload():
    """Return the payload for a new workflow. It stays disabled until publish."""
    return {
        "key": "",
        "name": "",
        "description": "",
        "category": "",
        "enabled": False,
        "approval_required": False,
        "manual_duration_minutes": None,
        "preserved": {},
        "schedule": _empty_schedule(),
        "inputs": [],
        "actions": {phase: [] for phase in PHASES},
    }


def builder_from_definition(definition, *, enabled=None):
    """Load a stored definition into the current builder payload.

    ``enabled`` is operational state. When the caller passes the database value,
    the checkbox follows that instead of a key that is no longer stored in YAML.
    """
    data = definition if isinstance(definition, dict) else {}
    if enabled is None:
        enabled = bool(data.get("enabled", True)) if data.get("key") else False
    if isinstance(definition, dict):
        data = normalize(definition)
    preserved = {
        key: value for key, value in data.items() if key not in managed_keys() and key not in PHASE_YAML_KEYS.values()
    }
    actions = {phase: [] for phase in PHASES}
    for phase, key in PHASE_YAML_KEYS.items():
        for item in data.get(key) or []:
            if isinstance(item, dict):
                actions[phase].append(_action_for_builder(item))
    manual = data.get("manual_duration_minutes")
    return {
        "key": data.get("key") or "",
        "name": data.get("name") or "",
        "description": data.get("description") or "",
        "category": data.get("category") or "",
        "enabled": bool(enabled),
        "approval_required": bool(data.get("approval_required", data.get("requires_approval", False))),
        "manual_duration_minutes": manual,
        "preserved": preserved,
        "schedule": _schedule_for_builder(data.get("schedule")),
        "inputs": [_input_for_builder(item) for item in data.get("inputs") or [] if isinstance(item, dict)],
        "actions": actions,
    }


def payload_to_raw(payload):
    """Return ``(raw_definition, errors)`` without applying semantic checks."""
    if not isinstance(payload, dict):
        return payload, [FieldError("", "invalid_type", "Builder payload must be an object.")]
    errors = []
    raw = {
        "key": payload.get("key"),
        "name": payload.get("name"),
        "description": payload.get("description", ""),
        "category": payload.get("category", ""),
        "approval_required": payload.get("approval_required", payload.get("requires_approval", False)),
    }
    manual = payload.get("manual_duration_minutes")
    if manual not in (None, ""):
        raw["manual_duration_minutes"] = manual
    preserved = payload.get("preserved", {})
    if preserved in (None, "", {}):
        preserved = {}
    elif not isinstance(preserved, dict):
        errors.append(FieldError("/preserved", "invalid_type", "Preserved keys must be an object."))
        preserved = {}
    for key, value in preserved.items():
        if key in managed_keys():
            continue
        raw.setdefault(key, value)
    inputs = []
    for index, item in enumerate(payload.get("inputs") or []):
        if not isinstance(item, dict):
            errors.append(
                FieldError(f"/inputs/{index}", "invalid_type", "Each input must be an object.", section="inputs")
            )
            continue
        inputs.append(_input_to_raw(item, f"/inputs/{index}", errors))
    if inputs:
        raw["inputs"] = inputs
    grouped = payload.get("actions") if isinstance(payload.get("actions"), dict) else {}
    # A canonical document posted as a payload uses pre_actions/actions/post_actions lists.
    if any(isinstance(payload.get(key), list) for key in PHASE_YAML_KEYS.values()) and not any(
        isinstance(grouped.get(phase), list) for phase in PHASES
    ):
        for key in PHASE_YAML_KEYS.values():
            if isinstance(payload.get(key), list):
                raw[key] = payload.get(key)
    else:
        for phase, key in PHASE_YAML_KEYS.items():
            phase_actions = grouped.get(phase) or []
            if not isinstance(phase_actions, list):
                errors.append(
                    FieldError(f"/{key}", "invalid_type", "Each phase must be a list of actions.", section="steps")
                )
                continue
            built = []
            for index, item in enumerate(phase_actions):
                if not isinstance(item, dict):
                    errors.append(
                        FieldError(f"/{key}/{index}", "invalid_type", "Each action must be an object.", section="steps")
                    )
                    continue
                built.append(_action_to_raw(item))
            if built:
                raw[key] = built
    schedule = payload.get("schedule")
    if isinstance(schedule, dict) and _schedule_has_values(schedule):
        raw["schedule"] = schedule
    return raw, errors


def actions_require_repository(definition):
    """Return True when any phase contains a Nautobot script action."""
    if not isinstance(definition, dict):
        return False
    for key in PHASE_YAML_KEYS.values():
        for action in definition.get(key) or []:
            if isinstance(action, dict) and action.get("type") == "nautobot":
                return True
    return False


def _schedule_has_values(schedule):
    if schedule.get("enabled"):
        return True
    servicenow = schedule.get("servicenow")
    if isinstance(servicenow, dict) and servicenow:
        return True
    for key in _SNOW_MANAGED:
        if schedule.get(key) not in (None, "", [], {}):
            return True
    if schedule.get("preserved_schedule") or schedule.get("preserved_servicenow"):
        return True
    if schedule.get("change_ticket_input_key"):
        return True
    return False


def _input_to_raw(item, pointer, errors):
    built = {
        "key": item.get("key"),
        "label": item.get("label"),
        "type": item.get("type") or "string",
    }
    if item.get("required"):
        built["required"] = True
    if item.get("default") not in (None, ""):
        built["default"] = item.get("default")
    if item.get("help_text"):
        built["help_text"] = item.get("help_text")
    if item.get("placeholder"):
        built["placeholder"] = item.get("placeholder")
    if item.get("depends_on"):
        built["depends_on"] = item.get("depends_on")
    show_if = item.get("show_if")
    if isinstance(show_if, dict) and show_if:
        built["show_if"] = show_if
    elif isinstance(show_if, str) and show_if.strip():
        built["show_if"] = show_if
    if item.get("validators"):
        built["validators"] = item.get("validators")
    elif item.get("regex"):
        built["regex"] = item.get("regex")
        if item.get("message"):
            built["message"] = item.get("message")
        if item.get("flags"):
            built["flags"] = item.get("flags")
    choices = item.get("choices_template") or item.get("choices")
    if choices not in (None, "", []):
        built["choices"] = choices
    if item.get("model"):
        built["model"] = item.get("model")
    query = item.get("filter_query")
    if query in (None, "", {}):
        raw_filter = item.get("filter")
        if isinstance(raw_filter, dict):
            query = raw_filter.get("query")
    if query not in (None, "", {}):
        built["filter"] = {"query": _parse_query(query, f"{pointer}/filter/query", errors)}
    if item.get("columns"):
        built["columns"] = [_column_to_raw(column) for column in item.get("columns") if isinstance(column, dict)]
    preserved = item.get("preserved") if isinstance(item.get("preserved"), dict) else {}
    for key, value in preserved.items():
        if key not in built:
            built[key] = value
    return built


def _column_to_raw(column):
    built = {"key": column.get("key"), "type": column.get("type") or "string"}
    if column.get("label"):
        built["label"] = column.get("label")
    if column.get("required"):
        built["required"] = True
    if column.get("unique"):
        built["unique"] = True
    if column.get("choices"):
        built["choices"] = column.get("choices")
    if column.get("validators"):
        built["validators"] = column.get("validators")
    preserved = column.get("preserved") if isinstance(column.get("preserved"), dict) else {}
    for key, value in preserved.items():
        if key not in built:
            built[key] = value
    return built


def _action_to_raw(item):
    built = {"type": item.get("type"), "name": item.get("name")}
    if item.get("when"):
        built["when"] = item.get("when")
    if item.get("script"):
        built["script"] = item.get("script")
    if item.get("with") not in (None, "", {}):
        built["with"] = item.get("with")
    if item.get("template_id") not in (None, ""):
        built["template_id"] = item.get("template_id")
    if item.get("extra_vars") not in (None, "", {}):
        built["extra_vars"] = item.get("extra_vars")
    if item.get("monitor") is not None:
        built["monitor"] = item.get("monitor")
    preserved = item.get("preserved") if isinstance(item.get("preserved"), dict) else {}
    for key, value in preserved.items():
        if key not in built:
            built[key] = value
    return built


def _parse_query(value, pointer, errors):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text.startswith("{{") or text.startswith("{%"):
        return value
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        errors.append(
            FieldError(
                pointer, "yaml", "Enter a YAML mapping, for example key: value.", section="inputs", field_label="Filter"
            )
        )
        return value
    return loaded


def _empty_schedule():
    return {
        "enabled": False,
        "integration_name": "",
        "change_input_key": "",
        "allowed_states": [],
        "timezone": "",
        "start_fields": [],
        "end_fields": [],
        "ticket_regex": "",
        "require_approved": None,
        "preserved_schedule": {},
        "preserved_servicenow": {},
    }


def _schedule_for_builder(raw_schedule):
    schedule = _empty_schedule()
    if not isinstance(raw_schedule, dict):
        return schedule
    servicenow = raw_schedule.get("servicenow") if isinstance(raw_schedule.get("servicenow"), dict) else {}
    preserved_schedule = {key: value for key, value in raw_schedule.items() if key != "servicenow"}
    change_input_key = servicenow.get("change_input_key") or preserved_schedule.pop("change_ticket_input_key", None)
    if isinstance(change_input_key, str):
        change_input_key = change_input_key.strip()
    else:
        change_input_key = ""
    schedule["enabled"] = bool(servicenow) or bool(change_input_key)
    integration_name = servicenow.get("integration_name")
    schedule["integration_name"] = integration_name.strip() if isinstance(integration_name, str) else ""
    schedule["change_input_key"] = change_input_key or ""
    schedule["allowed_states"] = list(servicenow.get("allowed_states") or [])
    timezone_name = servicenow.get("timezone")
    schedule["timezone"] = timezone_name.strip() if isinstance(timezone_name, str) else ""
    schedule["start_fields"] = list(servicenow.get("start_fields") or [])
    schedule["end_fields"] = list(servicenow.get("end_fields") or [])
    ticket_regex = servicenow.get("ticket_regex")
    schedule["ticket_regex"] = ticket_regex if isinstance(ticket_regex, str) else ""
    if "require_approved" in servicenow:
        schedule["require_approved"] = bool(servicenow.get("require_approved"))
    schedule["preserved_schedule"] = preserved_schedule
    schedule["preserved_servicenow"] = {key: value for key, value in servicenow.items() if key not in _SNOW_MANAGED}
    return schedule


def _input_for_builder(item):
    validators = item.get("validators")
    if not validators and item.get("regex"):
        folded = {"regex": item.get("regex")}
        if item.get("message"):
            folded["message"] = item["message"]
        if item.get("flags"):
            folded["flags"] = item["flags"]
        validators = [folded]
    raw_choices = item.get("choices")
    choices_template = raw_choices if isinstance(raw_choices, str) else ""
    choices = []
    if isinstance(raw_choices, list):
        for choice in raw_choices:
            if isinstance(choice, dict):
                choices.append(
                    {
                        "value": choice.get("value"),
                        "label": choice.get("label")
                        if choice.get("label") not in (None, "")
                        else str(choice.get("value")),
                    }
                )
            elif isinstance(choice, (str, int, float)) and not isinstance(choice, bool):
                choices.append({"value": choice, "label": str(choice)})
    filter_query = ""
    raw_filter = item.get("filter")
    preserved = {key: value for key, value in item.items() if key not in _INPUT_MANAGED}
    if isinstance(raw_filter, dict):
        query = raw_filter.get("query")
        if isinstance(query, str):
            filter_query = query
        elif query is not None:
            filter_query = yaml.safe_dump(query, sort_keys=False, default_flow_style=False).strip()
        extra_filter = {key: value for key, value in raw_filter.items() if key != "query"}
        if extra_filter:
            preserved["filter"] = extra_filter
    return {
        "key": item.get("key") or "",
        "label": item.get("label") or item.get("key") or "",
        "type": item.get("type") or "string",
        "required": bool(item.get("required", False)),
        "default": item.get("default"),
        "depends_on": list(item.get("depends_on") or []),
        "show_if": item.get("show_if") if isinstance(item.get("show_if"), (dict, str)) else {},
        "validators": validators if isinstance(validators, list) else [],
        "choices": choices,
        "choices_template": choices_template,
        "model": item.get("model") or "",
        "filter_query": filter_query,
        "columns": [_column_for_builder(column) for column in item.get("columns") or [] if isinstance(column, dict)],
        "preserved": preserved,
        "help_text": item.get("help_text") or "",
        "placeholder": item.get("placeholder") or "",
    }


def _column_for_builder(column):
    preserved = {key: value for key, value in column.items() if key not in _COLUMN_MANAGED}
    return {
        "key": column.get("key") or "",
        "label": column.get("label") or "",
        "type": column.get("type") or "string",
        "required": bool(column.get("required", False)),
        "unique": bool(column.get("unique", False)),
        "choices": column.get("choices") or [],
        "validators": column.get("validators") if isinstance(column.get("validators"), list) else [],
        "preserved": preserved,
    }


def _action_for_builder(item):
    preserved = {key: value for key, value in item.items() if key not in _ACTION_MANAGED}
    return {
        "type": item.get("type") or "nautobot",
        "name": item.get("name") or "",
        "when": item.get("when") or "",
        "script": item.get("script") or "",
        "with": item.get("with") if isinstance(item.get("with"), dict) else item.get("with") or "",
        "template_id": item.get("template_id"),
        "extra_vars": item.get("extra_vars")
        if isinstance(item.get("extra_vars"), dict)
        else item.get("extra_vars") or "",
        "monitor": item.get("monitor"),
        "preserved": preserved,
    }
