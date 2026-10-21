"""Serialize the workflow builder model to the definition git sync already stores.

The builder payload is a JSON object edited in the UI. ``definition_from_builder``
validates it and returns the canonical ``raw_definition`` dict. ``definition_to_yaml``
dumps that dict. ``builder_from_definition`` loads a stored definition back into the
builder, keeping keys the form does not edit.
"""

import re

import yaml
import zoneinfo

INPUT_TYPES = (
    "string",
    "integer",
    "boolean",
    "choice",
    "multichoice",
    "object",
    "multiobject",
    "list",
    "listdict",
)
COLUMN_TYPES = ("string", "integer", "boolean", "choice", "number")
OBJECT_TYPES = ("object", "multiobject")
CHOICE_TYPES = ("choice", "multichoice")
ACTION_TYPES = ("nautobot", "awx")
PHASES = ("pre", "main", "post")
PHASE_YAML_KEYS = {"pre": "pre_actions", "main": "actions", "post": "post_actions"}
ACTION_KEY_ALIASES = {
    "pre": ("pre_actions", "pre-actions", "before_actions", "before-actions"),
    "main": ("actions", "main_actions", "main-actions"),
    "post": ("post_actions", "post-actions", "after_actions", "after-actions"),
}
VALIDATOR_FLAGS = ("IGNORECASE", "MULTILINE", "DOTALL", "VERBOSE")
SLUG_RE = re.compile(r"^[-a-zA-Z0-9_]+$")
MODEL_RE = re.compile(r"^[A-Za-z_][\w]*\.[A-Za-z_][\w]*$")
WHEN_MAX_LENGTH = 200
KEY_MAX_LENGTH = 50
NAME_MAX_LENGTH = 200
LABEL_MAX_LENGTH = 200
CATEGORY_MAX_LENGTH = 100
SCRIPT_PATH_MAX_LENGTH = 254

TOP_LEVEL_MANAGED = {
    "key",
    "name",
    "description",
    "category",
    "enabled",
    "approval_required",
    "requires_approval",
    "manual_duration_minutes",
    "schedule",
    "inputs",
    "actions",
    "preserved",
    "source_repo",
    *{alias for aliases in ACTION_KEY_ALIASES.values() for alias in aliases},
}
INPUT_MANAGED = {
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
}
COLUMN_MANAGED = {"key", "label", "type", "required", "unique", "choices", "validators", "preserved"}
ACTION_MANAGED = {
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
SERVICENOW_MANAGED = {
    "integration_name",
    "change_input_key",
    "allowed_states",
    "timezone",
    "start_fields",
    "end_fields",
    "ticket_regex",
    "require_approved",
}


class BuilderValidationError(Exception):
    """Raised when a builder payload cannot be serialized.

    ``errors`` is a list of ``{"path", "message"}`` objects the UI can render.
    """

    def __init__(self, errors):
        """Store field errors and a single summary message."""
        self.errors = errors
        summary = "; ".join(f"{item['path'] or 'definition'}: {item['message']}" for item in errors)
        super().__init__(summary or "Workflow definition is invalid.")


def definition_from_builder(payload):
    """Validate a builder payload and return the canonical raw definition.

    Args:
        payload: Builder JSON object posted by the workflow builder.

    Returns:
        A dict suitable for ``Workflow.raw_definition`` and for YAML export.

    Raises:
        BuilderValidationError: The payload is missing required fields or uses an unsupported shape.
    """
    if not isinstance(payload, dict):
        raise BuilderValidationError([{"path": "", "message": "Builder payload must be an object."}])

    errors = []
    definition = _metadata(payload, errors)
    inputs = _inputs(payload.get("inputs", []), errors)
    schedule = _schedule(payload.get("schedule"), [item["key"] for item in inputs], errors)
    actions = _actions(payload.get("actions", {}), errors)

    preserved = _mapping(payload.get("preserved"), "preserved", errors)
    _check_input_references(inputs, errors)
    _check_duplicate_action_names(actions, errors)
    if errors:
        raise BuilderValidationError(errors)

    for key, value in preserved.items():
        if key in TOP_LEVEL_MANAGED or key in definition:
            continue
        definition[key] = value
    if schedule:
        definition["schedule"] = schedule
    definition["inputs"] = inputs
    for phase in PHASES:
        phase_actions = actions[phase]
        if phase_actions:
            definition[PHASE_YAML_KEYS[phase]] = phase_actions
    _reject_unsafe_templates(definition, errors)
    if errors:
        raise BuilderValidationError(errors)
    return definition


def definition_to_yaml(definition):
    """Dump a canonical definition to YAML text.

    Args:
        definition: Dict returned by ``definition_from_builder``.

    Returns:
        YAML text ending in a newline. Key order matches the dict.
    """
    dumped = yaml.safe_dump(
        definition,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    if not dumped.endswith("\n"):
        dumped += "\n"
    return dumped


def builder_from_definition(definition):
    """Convert a stored raw definition into a builder payload.

    Unknown keys are returned under ``preserved`` so a later save writes them back.
    Action lists are accepted under the same aliases git sync understands.

    Args:
        definition: Parsed workflow YAML or ``Workflow.raw_definition``.

    Returns:
        Builder payload accepted by ``definition_from_builder``.
    """
    data = definition if isinstance(definition, dict) else {}
    if "approval_required" in data:
        approval_required = bool(data.get("approval_required"))
    else:
        approval_required = bool(data.get("requires_approval", False))

    preserved = {key: value for key, value in data.items() if key not in TOP_LEVEL_MANAGED}
    manual_duration = data.get("manual_duration_minutes")
    if isinstance(manual_duration, bool) or not isinstance(manual_duration, int):
        manual_duration = None

    return {
        "key": data.get("key") or "",
        "name": data.get("name") or data.get("key") or "",
        "description": data.get("description") or "",
        "category": data.get("category") or "",
        "enabled": bool(data.get("enabled", True)),
        "approval_required": approval_required,
        "manual_duration_minutes": manual_duration,
        "preserved": preserved,
        "schedule": _schedule_for_builder(data.get("schedule")),
        "inputs": [_input_for_builder(item) for item in data.get("inputs") or [] if isinstance(item, dict)],
        "actions": {
            phase: [_action_for_builder(item) for item in _action_alias_list(data, phase) if isinstance(item, dict)]
            for phase in PHASES
        },
    }


def empty_builder_payload():
    """Return the builder payload used for a new workflow."""
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


def integration_name_from_definition(definition, default="ServiceNow"):
    """Return the ServiceNow External Integration name stored on a definition.

    Args:
        definition: Canonical raw definition, or a dict shaped like one.
        default: Name used when the workflow does not set ``integration_name``.

    Returns:
        The configured integration name, or ``default``.
    """
    schedule = definition.get("schedule") if isinstance(definition, dict) else None
    servicenow = schedule.get("servicenow") if isinstance(schedule, dict) else None
    if not isinstance(servicenow, dict):
        return default
    name = servicenow.get("integration_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return default


def actions_require_repository(definition):
    """Return True when the definition has a Nautobot script action.

    Args:
        definition: Canonical raw definition.

    Returns:
        True if any phase contains ``type: nautobot``.
    """
    if not isinstance(definition, dict):
        return False
    for phase in PHASES:
        for action in definition.get(PHASE_YAML_KEYS[phase]) or []:
            if isinstance(action, dict) and action.get("type") == "nautobot":
                return True
    return False


def _metadata(payload, errors):
    """Return the canonical metadata fields."""
    key = _required_slug(payload.get("key"), "key", KEY_MAX_LENGTH, errors)
    name = _required_text(payload.get("name"), "name", NAME_MAX_LENGTH, errors)
    description = _optional_text(payload.get("description"), "description", None, errors)
    category = _optional_text(payload.get("category"), "category", CATEGORY_MAX_LENGTH, errors)
    if "approval_required" in payload:
        approval_required = bool(payload.get("approval_required"))
    else:
        approval_required = bool(payload.get("requires_approval", False))
    definition = {
        "key": key,
        "name": name or key,
        "description": description,
        "category": category,
        "enabled": bool(payload.get("enabled", False)),
        "approval_required": approval_required,
    }
    manual_duration = payload.get("manual_duration_minutes")
    if manual_duration not in (None, ""):
        if isinstance(manual_duration, bool) or not isinstance(manual_duration, int) or manual_duration < 0:
            if isinstance(manual_duration, str) and manual_duration.isdigit():
                definition["manual_duration_minutes"] = int(manual_duration)
            else:
                errors.append(
                    {
                        "path": "manual_duration_minutes",
                        "message": "Enter a whole number of minutes, or leave this empty.",
                    }
                )
        else:
            definition["manual_duration_minutes"] = manual_duration
    return definition


def _inputs(raw_inputs, errors):
    """Return canonical input dicts in builder order."""
    if raw_inputs in (None, ""):
        return []
    if not isinstance(raw_inputs, list):
        errors.append({"path": "inputs", "message": "Inputs must be a list."})
        return []

    ordered = []
    for index, item in enumerate(raw_inputs):
        order = index
        if isinstance(item, dict) and isinstance(item.get("order"), int) and not isinstance(item.get("order"), bool):
            order = item["order"]
        ordered.append((order, index, item))
    ordered.sort(key=lambda item: (item[0], item[1]))

    inputs = []
    seen = set()
    for _order, index, item in ordered:
        path = f"inputs[{index}]"
        if not isinstance(item, dict):
            errors.append({"path": path, "message": "Each input must be an object."})
            continue
        built = _one_input(item, path, errors)
        if built["key"]:
            if built["key"] in seen:
                errors.append({"path": f"{path}.key", "message": f"Duplicate input key '{built['key']}'."})
            seen.add(built["key"])
        inputs.append(built)
    return inputs


def _one_input(item, path, errors):
    """Return one canonical input dict."""
    key = _required_slug(item.get("key"), f"{path}.key", KEY_MAX_LENGTH, errors)
    label = _optional_text(item.get("label"), f"{path}.label", LABEL_MAX_LENGTH, errors) or key
    input_type = item.get("type") or "string"
    if input_type not in INPUT_TYPES:
        errors.append({"path": f"{path}.type", "message": f"Unsupported input type '{input_type}'."})
        input_type = "string"

    built = {
        "key": key,
        "label": label,
        "type": input_type,
        "required": bool(item.get("required", False)),
    }
    default = _normalize_default(item.get("default"), input_type, f"{path}.default", errors)
    if default is not None:
        built["default"] = default

    depends_on = _string_list(item.get("depends_on"), f"{path}.depends_on", errors)
    if depends_on:
        built["depends_on"] = depends_on

    show_if = _show_if(item.get("show_if"), f"{path}.show_if", errors)
    if show_if:
        built["show_if"] = show_if

    validators = _validators(item, f"{path}.validators", errors)
    if validators:
        built["validators"] = validators

    if input_type in CHOICE_TYPES:
        raw_choices = item.get("choices")
        if not raw_choices and item.get("choices_template"):
            raw_choices = item.get("choices_template")
        choices = _choices(raw_choices, f"{path}.choices", errors)
        if not choices:
            errors.append({"path": f"{path}.choices", "message": "Add at least one choice."})
        built["choices"] = choices
        if isinstance(choices, list) and "default" in built:
            allowed = set()
            for choice in choices:
                if isinstance(choice, dict):
                    allowed.add(choice.get("value"))
                    allowed.add(str(choice.get("value")))
            default_values = built["default"] if isinstance(built["default"], list) else [built["default"]]
            if any(value not in allowed and str(value) not in allowed for value in default_values):
                errors.append({"path": f"{path}.default", "message": "Default must be one of the choices."})

    if input_type in OBJECT_TYPES:
        model = item.get("model") or ""
        if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
            errors.append({"path": f"{path}.model", "message": "Select a model such as dcim.Device."})
        else:
            built["model"] = model
        filter_query = _filter_query(item, path, errors)
        if filter_query:
            built["filter"] = {"query": filter_query}

    if input_type == "listdict":
        columns = _columns(item.get("columns"), f"{path}.columns", errors)
        if not columns:
            errors.append({"path": f"{path}.columns", "message": "Add at least one column."})
        built["columns"] = columns

    preserved = dict(item["preserved"]) if isinstance(item.get("preserved"), dict) else {}
    extra_filter = preserved.pop("filter", None)
    if isinstance(extra_filter, dict):
        merged_filter = dict(extra_filter)
        merged_filter.update(built.get("filter") or {})
        if merged_filter:
            built["filter"] = merged_filter
    for extra_key, extra_value in preserved.items():
        if extra_key not in built:
            built[extra_key] = extra_value
    return built


def _schedule(raw_schedule, input_keys, errors):
    """Return the canonical schedule block, or None when it should be omitted."""
    if raw_schedule in (None, "", {}):
        return None
    if not isinstance(raw_schedule, dict):
        errors.append({"path": "schedule", "message": "Schedule must be an object."})
        return None

    preserved_schedule = _mapping(raw_schedule.get("preserved_schedule"), "schedule.preserved_schedule", errors)
    preserved_servicenow = _mapping(raw_schedule.get("preserved_servicenow"), "schedule.preserved_servicenow", errors)
    if not raw_schedule.get("enabled"):
        return preserved_schedule or None

    servicenow = dict(preserved_servicenow)
    integration_name = _optional_text(raw_schedule.get("integration_name"), "schedule.integration_name", 100, errors)
    if integration_name:
        servicenow["integration_name"] = integration_name

    change_input_key = _optional_text(raw_schedule.get("change_input_key"), "schedule.change_input_key", 50, errors)
    if not change_input_key:
        errors.append(
            {
                "path": "schedule.change_input_key",
                "message": "Choose the input that collects the ServiceNow change ticket.",
            }
        )
    elif change_input_key not in input_keys:
        errors.append(
            {
                "path": "schedule.change_input_key",
                "message": f"No input named '{change_input_key}' exists on this workflow.",
            }
        )
    else:
        servicenow["change_input_key"] = change_input_key

    for field in ("allowed_states", "start_fields", "end_fields"):
        values = _string_list(raw_schedule.get(field), f"schedule.{field}", errors)
        if values:
            servicenow[field] = values

    timezone_name = _optional_text(raw_schedule.get("timezone"), "schedule.timezone", 64, errors)
    if timezone_name:
        if not _timezone_exists(timezone_name):
            errors.append({"path": "schedule.timezone", "message": f"Unknown timezone '{timezone_name}'."})
        else:
            servicenow["timezone"] = timezone_name

    ticket_regex = _optional_text(raw_schedule.get("ticket_regex"), "schedule.ticket_regex", 200, errors)
    if ticket_regex:
        try:
            re.compile(ticket_regex)
        except re.error:
            errors.append(
                {"path": "schedule.ticket_regex", "message": "Ticket pattern is not a valid regular expression."}
            )
        else:
            servicenow["ticket_regex"] = ticket_regex

    if raw_schedule.get("require_approved") is not None and "require_approved" in raw_schedule:
        servicenow["require_approved"] = bool(raw_schedule.get("require_approved"))

    schedule = dict(preserved_schedule)
    schedule["servicenow"] = servicenow
    return schedule


def _actions(raw_actions, errors):
    """Return canonical actions keyed by phase."""
    phases = {phase: [] for phase in PHASES}
    if raw_actions in (None, "", {}):
        return phases
    if not isinstance(raw_actions, dict):
        errors.append({"path": "actions", "message": "Actions must be grouped into pre, main, and post."})
        return phases

    for phase in PHASES:
        raw_list = raw_actions.get(phase, [])
        path = f"actions.{phase}"
        if raw_list in (None, ""):
            continue
        if not isinstance(raw_list, list):
            errors.append({"path": path, "message": "Each phase must be a list of actions."})
            continue
        for index, item in enumerate(raw_list):
            action_path = f"{path}[{index}]"
            if not isinstance(item, dict):
                errors.append({"path": action_path, "message": "Each action must be an object."})
                continue
            phases[phase].append(_one_action(item, action_path, errors))
    return phases


def _check_duplicate_action_names(actions, errors):
    """Reject the same action name in more than one step."""
    seen = {}
    for phase in PHASES:
        for index, action in enumerate(actions.get(phase) or []):
            name = action.get("name")
            if not name:
                continue
            if name in seen:
                errors.append(
                    {
                        "path": f"actions.{phase}[{index}].name",
                        "message": f"Duplicate action name '{name}'.",
                    }
                )
            else:
                seen[name] = f"actions.{phase}[{index}]"


def _one_action(item, path, errors):
    """Return one canonical action dict."""
    action_type = item.get("type")
    if action_type not in ACTION_TYPES:
        errors.append({"path": f"{path}.type", "message": "Action type must be nautobot or awx."})
        action_type = "nautobot"
    name = _optional_text(item.get("name"), f"{path}.name", NAME_MAX_LENGTH, errors) or action_type
    built = {"type": action_type, "name": name}
    when = _optional_text(item.get("when"), f"{path}.when", WHEN_MAX_LENGTH, errors)
    if when:
        if "{{" not in when and "{%" not in when:
            errors.append(
                {
                    "path": f"{path}.when",
                    "message": "Wrap the condition in `{{ }}`, for example `{{ inputs.confirm }}`",
                }
            )
        built["when"] = when

    if action_type == "nautobot":
        script = item.get("script") or ""
        if not isinstance(script, str):
            script = ""
        script = script.strip().lstrip("/")
        if not script or ".." in script.split("/"):
            errors.append({"path": f"{path}.script", "message": "Choose a script from the source repository."})
        elif len(script) > SCRIPT_PATH_MAX_LENGTH:
            errors.append({"path": f"{path}.script", "message": "Script path is too long."})
        else:
            built["script"] = script
        with_vars = _yaml_mapping(item.get("with"), f"{path}.with", errors)
        if with_vars:
            built["with"] = with_vars
    else:
        template_id = item.get("template_id")
        if isinstance(template_id, str) and template_id.isdigit():
            template_id = int(template_id)
        if isinstance(template_id, bool) or not isinstance(template_id, int) or template_id <= 0:
            errors.append({"path": f"{path}.template_id", "message": "Enter an AWX job template ID."})
        else:
            built["template_id"] = template_id
        extra_vars = _yaml_mapping(item.get("extra_vars"), f"{path}.extra_vars", errors)
        if extra_vars:
            built["extra_vars"] = extra_vars
        if item.get("monitor") is not None:
            built["monitor"] = bool(item.get("monitor"))

    preserved = item.get("preserved") if isinstance(item.get("preserved"), dict) else {}
    for extra_key, extra_value in preserved.items():
        if extra_key not in built:
            built[extra_key] = extra_value
    return built


def _schedule_for_builder(raw_schedule):
    """Return the schedule section of a builder payload."""
    schedule = _empty_schedule()
    if not isinstance(raw_schedule, dict):
        return schedule
    servicenow = raw_schedule.get("servicenow") if isinstance(raw_schedule.get("servicenow"), dict) else {}
    preserved_schedule = {key: value for key, value in raw_schedule.items() if key != "servicenow"}
    change_input_key = servicenow.get("change_input_key")
    if not isinstance(change_input_key, str) or not change_input_key.strip():
        legacy = preserved_schedule.pop("change_ticket_input_key", None)
        change_input_key = legacy.strip() if isinstance(legacy, str) else ""
    else:
        change_input_key = change_input_key.strip()

    schedule["enabled"] = bool(servicenow) or bool(change_input_key)
    integration_name = servicenow.get("integration_name")
    schedule["integration_name"] = integration_name.strip() if isinstance(integration_name, str) else ""
    schedule["change_input_key"] = change_input_key
    schedule["allowed_states"] = _clean_string_list(servicenow.get("allowed_states"))
    timezone_name = servicenow.get("timezone")
    schedule["timezone"] = timezone_name.strip() if isinstance(timezone_name, str) else ""
    schedule["start_fields"] = _clean_string_list(servicenow.get("start_fields"))
    schedule["end_fields"] = _clean_string_list(servicenow.get("end_fields"))
    ticket_regex = servicenow.get("ticket_regex")
    schedule["ticket_regex"] = ticket_regex if isinstance(ticket_regex, str) else ""
    if "require_approved" in servicenow:
        schedule["require_approved"] = bool(servicenow.get("require_approved"))
    else:
        schedule["require_approved"] = None
    schedule["preserved_schedule"] = preserved_schedule
    schedule["preserved_servicenow"] = {
        key: value for key, value in servicenow.items() if key not in SERVICENOW_MANAGED
    }
    return schedule


def _empty_schedule():
    """Return a schedule section with ServiceNow integration disabled."""
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


def _input_for_builder(item):
    """Return one input in builder shape, preserving unknown keys."""
    validators = item.get("validators")
    if not validators and item.get("regex"):
        folded = {"regex": item.get("regex")}
        if item.get("message"):
            folded["message"] = item["message"]
        if item.get("flags"):
            folded["flags"] = item["flags"]
        validators = [folded]
    filter_query = ""
    raw_filter = item.get("filter")
    if isinstance(raw_filter, dict) and raw_filter.get("query") is not None:
        query = raw_filter.get("query")
        filter_query = query if isinstance(query, str) else _mapping_to_yaml(query)
    raw_choices = item.get("choices")
    choices_template = raw_choices if isinstance(raw_choices, str) else ""
    if isinstance(raw_choices, list):
        choices = []
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
    else:
        choices = []
    preserved = {key: value for key, value in item.items() if key not in INPUT_MANAGED}
    if isinstance(raw_filter, dict):
        extra_filter = {key: value for key, value in raw_filter.items() if key != "query"}
        if extra_filter:
            preserved["filter"] = extra_filter
    return {
        "key": item.get("key") or "",
        "label": item.get("label") or item.get("key") or "",
        "type": item.get("type") or "string",
        "required": bool(item.get("required", False)),
        "default": item.get("default"),
        "depends_on": _clean_string_list(item.get("depends_on")),
        "show_if": item.get("show_if") if isinstance(item.get("show_if"), dict) else {},
        "validators": validators if isinstance(validators, list) else [],
        "choices": choices,
        "choices_template": choices_template,
        "model": item.get("model") or "",
        "filter_query": filter_query,
        "columns": [_column_for_builder(column) for column in item.get("columns") or [] if isinstance(column, dict)],
        "preserved": preserved,
    }


def _column_for_builder(column):
    """Return one listdict column in builder shape."""
    return {
        "key": column.get("key") or "",
        "label": column.get("label") or "",
        "type": column.get("type") or "string",
        "required": bool(column.get("required", False)),
        "unique": bool(column.get("unique", False)),
        "choices": column.get("choices") if isinstance(column.get("choices"), list) else [],
        "validators": column.get("validators") if isinstance(column.get("validators"), list) else [],
        "preserved": {key: value for key, value in column.items() if key not in COLUMN_MANAGED},
    }


def _action_for_builder(item):
    """Return one action in builder shape."""
    return {
        "type": item.get("type") or "",
        "name": item.get("name") or "",
        "when": item.get("when") or "",
        "script": (item.get("script") or "").lstrip("/"),
        "with": _mapping_to_yaml(item.get("with")) if isinstance(item.get("with"), dict) else (item.get("with") or ""),
        "template_id": item.get("template_id"),
        "extra_vars": _mapping_to_yaml(item.get("extra_vars")) if isinstance(item.get("extra_vars"), dict) else "",
        "monitor": bool(item.get("monitor")) if "monitor" in item else None,
        "preserved": {key: value for key, value in item.items() if key not in ACTION_MANAGED},
    }


def _action_alias_list(data, phase):
    """Return the first present action list for a phase, honoring YAML aliases."""
    for key in ACTION_KEY_ALIASES[phase]:
        value = data.get(key)
        if value is None:
            continue
        return value if isinstance(value, list) else []
    return []


def _columns(raw_columns, path, errors):
    """Return canonical listdict columns."""
    if not isinstance(raw_columns, list):
        if raw_columns not in (None, ""):
            errors.append({"path": path, "message": "Columns must be a list."})
        return []
    columns = []
    seen = set()
    for index, column in enumerate(raw_columns):
        column_path = f"{path}[{index}]"
        if not isinstance(column, dict):
            errors.append({"path": column_path, "message": "Each column must be an object."})
            continue
        key = _required_slug(column.get("key"), f"{column_path}.key", KEY_MAX_LENGTH, errors)
        if key in seen:
            errors.append({"path": f"{column_path}.key", "message": f"Duplicate column key '{key}'."})
        seen.add(key)
        column_type = column.get("type") or "string"
        if column_type not in COLUMN_TYPES:
            errors.append({"path": f"{column_path}.type", "message": f"Unsupported column type '{column_type}'."})
            column_type = "string"
        built = {"key": key, "type": column_type}
        label = _optional_text(column.get("label"), f"{column_path}.label", LABEL_MAX_LENGTH, errors)
        if label:
            built["label"] = label
        if column.get("required"):
            built["required"] = True
        if column.get("unique"):
            built["unique"] = True
        if column_type == "choice":
            choices = _choices(column.get("choices"), f"{column_path}.choices", errors)
            if not choices:
                errors.append({"path": f"{column_path}.choices", "message": "Choice columns need at least one choice."})
            built["choices"] = choices
        validators = _validators(column, f"{column_path}.validators", errors)
        if validators:
            built["validators"] = validators
        preserved = column.get("preserved") if isinstance(column.get("preserved"), dict) else {}
        for extra_key, extra_value in preserved.items():
            if extra_key not in built:
                built[extra_key] = extra_value
        columns.append(built)
    return columns


def _choices(raw_choices, path, errors):
    """Return canonical ``{value, label}`` choice entries.

    A Jinja string is kept as-is so documented workflows stay editable. Scalar list
    items become ``{value, label}`` pairs.
    """
    if raw_choices in (None, "", []):
        return []
    if isinstance(raw_choices, str):
        text = raw_choices.strip()
        if "{{" in text or "{%" in text:
            return text
        errors.append({"path": path, "message": "Choices must be a list of value and label."})
        return []
    if not isinstance(raw_choices, list):
        errors.append({"path": path, "message": "Choices must be a list of value and label."})
        return []
    choices = []
    for index, choice in enumerate(raw_choices):
        choice_path = f"{path}[{index}]"
        if isinstance(choice, (str, int, float)) and not isinstance(choice, bool):
            value = choice.strip() if isinstance(choice, str) else choice
            if value in ("", None):
                errors.append({"path": f"{choice_path}.value", "message": "Each choice needs a value."})
                continue
            choices.append({"value": value, "label": str(value)})
            continue
        if not isinstance(choice, dict):
            errors.append({"path": choice_path, "message": "Each choice must be an object with value and label."})
            continue
        value = choice.get("value")
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "") or not isinstance(value, (str, int, float)) or isinstance(value, bool):
            errors.append({"path": f"{choice_path}.value", "message": "Each choice needs a value."})
            continue
        label = choice.get("label")
        if label in (None, ""):
            label = str(value)
        elif not isinstance(label, str):
            label = str(label)
        choices.append({"value": value, "label": label.strip() or str(value)})
    return choices


def _validators(item, path, errors):
    """Normalize a validators list, including the legacy root regex shorthand."""
    raw_validators = item.get("validators") if isinstance(item, dict) else None
    if not raw_validators and isinstance(item, dict) and item.get("regex"):
        folded = {"regex": item.get("regex")}
        if item.get("message"):
            folded["message"] = item["message"]
        if item.get("flags"):
            folded["flags"] = item["flags"]
        raw_validators = [folded]
    if raw_validators in (None, "", []):
        return []
    if not isinstance(raw_validators, list):
        errors.append({"path": path, "message": "Validators must be a list."})
        return []
    validators = []
    for index, validator in enumerate(raw_validators):
        validator_path = f"{path}[{index}]"
        if not isinstance(validator, dict):
            errors.append({"path": validator_path, "message": "Each validator must be an object."})
            continue
        regex = validator.get("regex")
        if not isinstance(regex, str) or not regex:
            errors.append({"path": f"{validator_path}.regex", "message": "Each validator needs a regular expression."})
            continue
        flags = validator.get("flags") or []
        if isinstance(flags, str):
            flags = [flag.strip() for flag in flags.split(",") if flag.strip()]
        if not isinstance(flags, list):
            errors.append({"path": f"{validator_path}.flags", "message": "Validator flags must be a list."})
            flags = []
        invalid_flags = [flag for flag in flags if flag not in VALIDATOR_FLAGS]
        if invalid_flags:
            errors.append(
                {
                    "path": f"{validator_path}.flags",
                    "message": f"Unsupported flag '{invalid_flags[0]}'.",
                }
            )
        try:
            re.compile(regex)
        except re.error:
            errors.append({"path": f"{validator_path}.regex", "message": "Regular expression is invalid."})
        built = {"regex": regex}
        message = validator.get("message")
        if isinstance(message, str) and message:
            built["message"] = message
        if flags and not invalid_flags:
            built["flags"] = flags
        validators.append(built)
    return validators


def _show_if(value, path, errors):
    """Return a canonical show_if mapping."""
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        errors.append({"path": path, "message": "show_if must map a controlling input to a value."})
        return None
    normalized = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            errors.append({"path": path, "message": "show_if keys must be input keys."})
            continue
        normalized[key.strip()] = _show_if_value(raw, f"{path}.{key}", errors)
    return normalized or None


def _show_if_value(value, path, errors):
    """Return a scalar or list of scalars for one show_if entry."""
    if isinstance(value, list):
        if not value:
            errors.append({"path": path, "message": "show_if needs at least one value."})
        scalars = []
        for item in value:
            scalar = _scalar(item, path, errors)
            if scalar is not None:
                scalars.append(scalar)
        return scalars
    scalar = _scalar(value, path, errors)
    return scalar


def _scalar(value, path, errors):
    """Return a JSON-safe scalar or record an error."""
    if isinstance(value, bool) or isinstance(value, (int, float, str)):
        if isinstance(value, str):
            return value.strip()
        return value
    errors.append({"path": path, "message": "Use a string, number, or boolean."})
    return None


def _normalize_default(value, input_type, path, errors):
    """Return a canonical default, or None when the input does not set one."""
    if value is None or value == "":
        return None
    if input_type == "boolean":
        if isinstance(value, bool):
            return value
        if value == "true":
            return True
        if value == "false":
            return False
        errors.append({"path": path, "message": "Boolean default must be true or false."})
        return None
    if input_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip())
        errors.append({"path": path, "message": "Integer default must be a whole number."})
        return None
    if input_type in ("list", "listdict", "multichoice", "multiobject"):
        if isinstance(value, list):
            return value
        errors.append({"path": path, "message": "Default must be a list."})
        return None
    if input_type == "object" and isinstance(value, dict):
        return value
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return value
    errors.append({"path": path, "message": "Default must be a string, number, or boolean."})
    return None


def _filter_query(item, path, errors):
    """Return the object filter query mapping."""
    if item.get("filter_query") not in (None, "", {}):
        return _yaml_mapping(item.get("filter_query"), f"{path}.filter_query", errors)
    raw_filter = item.get("filter")
    if isinstance(raw_filter, dict) and raw_filter.get("query") not in (None, "", {}):
        return _yaml_mapping(raw_filter.get("query"), f"{path}.filter.query", errors)
    return None


def _yaml_mapping(value, path, errors):
    """Parse a dict or YAML mapping string."""
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError:
            errors.append({"path": path, "message": "Enter a YAML mapping, for example key: value."})
            return None
        if value in (None, {}):
            return None
    if not isinstance(value, dict):
        errors.append({"path": path, "message": "Enter a YAML mapping, for example key: value."})
        return None
    return value


def _mapping_to_yaml(value):
    """Dump a mapping to a YAML string for a textarea."""
    if not isinstance(value, dict) or not value:
        return ""
    return yaml.safe_dump(value, sort_keys=False, default_flow_style=False).strip()


def _mapping(value, path, errors):
    """Return a dict, recording an error when the value has another type."""
    if value in (None, "", {}):
        return {}
    if not isinstance(value, dict):
        errors.append({"path": path, "message": "Expected an object."})
        return {}
    return dict(value)


def _string_list(value, path, errors):
    """Normalize a list of strings, or a comma-separated string, into a list."""
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",")]
    if not isinstance(value, list):
        errors.append({"path": path, "message": "Enter a list of values."})
        return []
    cleaned = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _clean_string_list(value):
    """Return a list of non-empty strings without recording errors."""
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",")]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _required_slug(value, path, max_length, errors):
    """Return a slug or record an error and return an empty string."""
    if not isinstance(value, str) or not value.strip():
        errors.append({"path": path, "message": "This field is required."})
        return ""
    value = value.strip()
    if not SLUG_RE.fullmatch(value) or len(value) > max_length:
        errors.append(
            {
                "path": path,
                "message": "Use letters, numbers, hyphens, or underscores (50 characters or fewer).",
            }
        )
        return ""
    return value


def _required_text(value, path, max_length, errors):
    """Return required text or record an error."""
    if not isinstance(value, str) or not value.strip():
        errors.append({"path": path, "message": "This field is required."})
        return ""
    value = value.strip()
    if max_length and len(value) > max_length:
        errors.append({"path": path, "message": f"Use {max_length} characters or fewer."})
        return ""
    return value


def _optional_text(value, path, max_length, errors):
    """Return optional text. Missing values become an empty string."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        errors.append({"path": path, "message": "Enter text."})
        return ""
    value = value.strip()
    if max_length and len(value) > max_length:
        errors.append({"path": path, "message": f"Use {max_length} characters or fewer."})
        return ""
    return value


def _timezone_exists(name):
    """Return True when ``name`` is a known IANA timezone or UTC."""
    if name.upper() == "UTC":
        return True
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


_ATTR = r"[a-z_][a-z0-9_]{0,63}"
_INPUT_REF = rf"inputs\.{_ATTR}(?:\.{_ATTR}){{0,3}}"
_RESULT_REF = rf"results\.{_ATTR}(?:\.{_ATTR}){{0,3}}"
_REF = rf"(?:{_INPUT_REF}|{_RESULT_REF})"
_COND_ATOM = rf"(?:not\s+)?{_REF}(?:\s+(?:==|!=)\s+'(?:[^'\\]|\\.)*'|\s+in\s+\[[^\]]*\]|\s+is\s+none)?"
_SINGLE_REF_RE = re.compile(rf"^{{\{{\s*{_REF}\s*\}}}}$")
_CONDITION_RE = re.compile(rf"^{{\{{\s*{_COND_ATOM}(?:\s+(?:and|or)\s+{_COND_ATOM})*\s*\}}}}$")
AUTHOR_CODE_MESSAGE = "You need the *Author templates and scripts* permission to change this field."
TEMPLATE_SOURCE_LIMIT = 10 * 1024
LOOKUP_TRANSFORMS = frozenset(
    {
        "exact",
        "iexact",
        "in",
        "contains",
        "icontains",
        "startswith",
        "istartswith",
        "isnull",
        "gt",
        "gte",
        "lt",
        "lte",
    }
)
BUILDER_MODEL_DENYLIST = (
    "users.*",
    "auth.*",
    "sessions.*",
    "admin.*",
    "extras.secret*",
    "extras.gitrepository",
    "extras.jobresult",
    "extras.objectchange",
    "django_celery_*",
    "nautobot_workflow_engine.*",
)
DEFAULT_MODEL_ALLOWLIST = (
    "dcim.*",
    "ipam.*",
    "circuits.*",
    "tenancy.*",
    "virtualization.*",
    "cloud.*",
    "wireless.*",
    "vpn.*",
    "load_balancers.*",
    "extras.tag",
    "extras.status",
    "extras.role",
    "extras.dynamicgroup",
)


def _check_input_references(inputs, errors):
    """Require depends_on and show_if targets to exist, and reject self-references and cycles."""
    keys = [item.get("key") for item in inputs if item.get("key")]
    keyset = set(keys)
    graph = {key: [] for key in keyset}
    for item in inputs:
        key = item.get("key")
        if not key:
            continue
        deps = list(item.get("depends_on") or [])
        show_if = item.get("show_if") if isinstance(item.get("show_if"), dict) else {}
        deps.extend(show_if.keys())
        for dep in deps:
            if dep == key:
                errors.append({"path": "inputs", "message": f"Input '{key}' cannot reference itself."})
                continue
            if dep not in keyset:
                errors.append({"path": "inputs", "message": f"Input '{key}' references unknown input '{dep}'."})
                continue
            graph[key].append(dep)

    visiting = set()
    visited = set()

    def walk(node, stack):
        if node in visiting:
            errors.append(
                {
                    "path": "inputs",
                    "message": f"Inputs reference each other in a cycle: {' -> '.join(stack + [node])}.",
                }
            )
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            walk(dep, stack + [node])
        visiting.remove(node)
        visited.add(node)

    for key in keys:
        walk(key, [])


def _reject_unsafe_templates(definition, errors):
    """Compile every template and reject sandbox escapes and syntax errors."""
    from jinja2 import TemplateSyntaxError
    from jinja2.exceptions import SecurityError

    from .executors import get_jinja_environment

    env = get_jinja_environment()

    def walk(value, path):
        if isinstance(value, str):
            if "{{" not in value and "{%" not in value:
                return
            if len(value.encode("utf-8")) > TEMPLATE_SOURCE_LIMIT:
                errors.append({"path": path, "message": "Template is longer than 10 kB."})
                return
            try:
                env.from_string(value).render({})
            except SecurityError as exc:
                errors.append({"path": path, "message": f"Template was rejected: {exc}"})
            except TemplateSyntaxError as exc:
                errors.append({"path": path, "message": f"Template syntax is invalid: {exc}"})
            except Exception:
                return
        elif isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(definition, "")


def is_safe_template_value(value):
    """Return True when a code field is a literal, a single reference, or condition-builder output."""
    if value in (None, "", {}, []):
        return True
    if isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return True
        text = value.strip()
        return bool(_SINGLE_REF_RE.match(text) or _CONDITION_RE.match(text))
    if isinstance(value, list):
        return all(is_safe_template_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and "{{" not in key and "{%" not in key and is_safe_template_value(item)
            for key, item in value.items()
        )
    return False


def _filter_query_value(item):
    """Return the filter query stored on an input, or None."""
    if not isinstance(item, dict):
        return None
    raw_filter = item.get("filter")
    if isinstance(raw_filter, dict):
        return raw_filter.get("query")
    return None


def _choices_need_author(old, new):
    """Jinja choice strings are code. Static choice lists are not."""
    if old == new:
        return False
    return isinstance(old, str) or isinstance(new, str)


def _actions_by_name(definition):
    """Index actions by phase and name."""
    found = {}
    if not isinstance(definition, dict):
        return found
    for phase, key in PHASE_YAML_KEYS.items():
        for action in definition.get(key) or []:
            if isinstance(action, dict) and action.get("name"):
                found[(phase, action.get("name"))] = action
    return found


def code_changes(old_definition, new_definition):
    """Return pointers for code fields whose new value is not in the safe subset.

    Labels, order, and unchanged templates are not changes. A script path change
    always counts, because choosing a script is a code action.
    """
    old_definition = old_definition if isinstance(old_definition, dict) else {}
    new_definition = new_definition if isinstance(new_definition, dict) else {}
    changes = []
    old_inputs = {
        item.get("key"): item
        for item in old_definition.get("inputs") or []
        if isinstance(item, dict) and item.get("key")
    }
    for index, item in enumerate(new_definition.get("inputs") or []):
        if not isinstance(item, dict):
            continue
        previous = old_inputs.get(item.get("key"), {})
        pointer = f"/inputs/{index}"
        old_query = _filter_query_value(previous)
        new_query = _filter_query_value(item)
        if old_query != new_query and not is_safe_template_value(new_query):
            changes.append(f"{pointer}/filter/query")
        if _choices_need_author(previous.get("choices"), item.get("choices")):
            changes.append(f"{pointer}/choices")
    old_actions = _actions_by_name(old_definition)
    for phase, key in PHASE_YAML_KEYS.items():
        for index, action in enumerate(new_definition.get(key) or []):
            if not isinstance(action, dict):
                continue
            previous = old_actions.get((phase, action.get("name")), {})
            pointer = f"/{key}/{index}"
            if (action.get("script") or "") != (previous.get("script") or ""):
                changes.append(f"{pointer}/script")
            for field in ("when", "with", "extra_vars"):
                if action.get(field) != previous.get(field) and not is_safe_template_value(action.get(field)):
                    changes.append(f"{pointer}/{field}")
    return changes


def _pattern_match(label, pattern):
    """Match a lower-case ``app.model`` label against an allow or deny pattern."""
    label = label.lower()
    pattern = pattern.lower()
    if pattern.endswith("*"):
        return label.startswith(pattern[:-1])
    return label == pattern


def _model_label(label):
    """Normalize ``app.Model`` or ``app.model`` to a lower-case label."""
    text = str(label or "").strip()
    if "." not in text:
        return ""
    app, model = text.split(".", 1)
    return f"{app}.{model}".lower()


def content_type_allowed(label):
    """Return True when a model may appear in the builder or in an options lookup.

    The denylist wins over ``builder_model_allowlist``.
    """
    from django.conf import settings

    normalized = _model_label(label)
    if not normalized:
        return False
    for pattern in BUILDER_MODEL_DENYLIST:
        if _pattern_match(normalized, pattern):
            return False
    configured = settings.PLUGINS_CONFIG.get("nautobot_workflow_engine", {}).get("builder_model_allowlist")
    allowlist = configured if isinstance(configured, (list, tuple)) and configured else DEFAULT_MODEL_ALLOWLIST
    return any(_pattern_match(normalized, pattern) for pattern in allowlist)


def script_path_allowed(path):
    """Return True when a script path stays inside the configured scripts directory."""
    from django.conf import settings

    configured = settings.PLUGINS_CONFIG.get("nautobot_workflow_engine", {}).get("scripts_dir", "scripts")
    scripts_dir = str(configured or "scripts").strip().strip("/")
    normalized = str(path or "").strip().lstrip("/")
    if not normalized or not scripts_dir:
        return False
    if ".." in normalized.split("/"):
        return False
    return normalized == scripts_dir or normalized.startswith(scripts_dir + "/")


def _lookup_field(model, name):
    """Return a concrete or relation field for one lookup hop."""
    try:
        return model._meta.get_field(name)
    except Exception:
        for field in model._meta.get_fields():
            if getattr(field, "attname", None) == name:
                return field
    return None


def lookup_allowed(model, lookup):
    """Return True when a filter lookup stays on allowlisted models.

    At most three relations may be traversed. The only trailing transforms are
    the documented exact/iexact/in/contains/comparison lookups.
    """
    if model is None or not isinstance(lookup, str) or not lookup or "{{" in lookup or "{%" in lookup:
        return False
    parts = lookup.split("__")
    current = model
    hops = 0
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        if is_last and part in LOOKUP_TRANSFORMS:
            return True
        field = _lookup_field(current, part)
        if field is None:
            return False
        if getattr(field, "is_relation", False):
            related = getattr(field, "related_model", None)
            if related is None:
                return False
            label = f"{related._meta.app_label}.{related.__name__}"
            if not content_type_allowed(label):
                return False
            hops += 1
            if hops > 3:
                return False
            if is_last:
                return True
            current = related
            continue
        rest = parts[index + 1 :]
        if not rest:
            return True
        return len(rest) == 1 and rest[0] in LOOKUP_TRANSFORMS
    return False


def builder_policy_errors(definition):
    """Return field errors for models and scripts the builder is not allowed to save.

    Git sync does not use this. Repository YAML can still name other models.
    """
    errors = []
    if not isinstance(definition, dict):
        return errors
    for index, item in enumerate(definition.get("inputs") or []):
        if not isinstance(item, dict):
            continue
        model_label = item.get("model")
        if model_label and not content_type_allowed(model_label):
            errors.append({"path": f"inputs[{index}].model", "message": "That model is not available in the builder."})
            continue
        query = _filter_query_value(item)
        if not model_label or not isinstance(query, dict):
            continue
        model = _model_for_label(model_label)
        if model is None:
            continue
        for lookup in query:
            if not isinstance(lookup, str) or "{{" in lookup or "{%" in lookup:
                continue
            if not lookup_allowed(model, lookup):
                errors.append(
                    {
                        "path": f"inputs[{index}].filter.query",
                        "message": f"Filter lookup '{lookup}' is not allowed.",
                    }
                )
    for phase, key in PHASE_YAML_KEYS.items():
        for index, action in enumerate(definition.get(key) or []):
            if not isinstance(action, dict):
                continue
            script = action.get("script")
            if script and not script_path_allowed(script):
                errors.append(
                    {
                        "path": f"actions.{phase}[{index}].script",
                        "message": "Choose a script from the scripts directory.",
                    }
                )
    return errors


def _model_for_label(label):
    """Return the model class for ``app.Model``, or None when it is not installed."""
    from django.apps import apps

    normalized = _model_label(label)
    if "." not in normalized:
        return None
    app, model = normalized.split(".", 1)
    try:
        return apps.get_model(app, model)
    except LookupError:
        return None
