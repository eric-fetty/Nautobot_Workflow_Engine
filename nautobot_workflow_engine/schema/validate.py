"""JSON Schema plus semantic checks. Nothing here writes to the database."""

from jsonschema import Draft202012Validator

from ..timezones import is_valid_timezone
from . import ACTION_TYPES, PHASE_YAML_KEYS, TEMPLATE_SOURCE_LIMIT, load_schema
from .errors import FieldError
from .policy import script_path_allowed

# Every code validate() can emit. Tests cover each one.
FIELD_ERROR_CODES = (
    "required",
    "min_length",
    "max_length",
    "pattern",
    "enum",
    "type",
    "minimum",
    "min_items",
    "invalid_type",
    "duplicate_key",
    "self_reference",
    "unknown_reference",
    "cycle",
    "choice_default",
    "duplicate_action",
    "model_required",
    "script_required",
    "script_missing",
    "repository_required",
    "script_path",
    "template_required",
    "when_expression",
    "template_syntax",
    "template_rejected",
    "template_too_long",
    "model_denied",
    "lookup_denied",
    "change_input_missing",
    "timezone_invalid",
    "reserved_key",
    "yaml",
)

_SCHEMA_CODE = {
    "required": "required",
    "minLength": "min_length",
    "maxLength": "max_length",
    "pattern": "pattern",
    "enum": "enum",
    "type": "type",
    "minimum": "minimum",
    "minItems": "min_items",
    "const": "enum",
    "oneOf": "type",
    "anyOf": "type",
}


def validate(canonical, *, repo=None, enforce_policy=False, check_scripts=False):
    """Return a list of :class:`FieldError`. An empty list means the definition can be stored.

    ``enforce_policy`` applies the builder model allowlist and ``scripts_dir`` rule.
    Git sync leaves it off. ``check_scripts`` looks up Nautobot script files on ``repo``.
    """
    if not isinstance(canonical, dict):
        return [FieldError("", "invalid_type", "Workflow definition must be an object.", section="details")]
    errors = []
    _schema_errors(canonical, errors)
    _semantic(canonical, errors, repo=repo, enforce_policy=enforce_policy, check_scripts=check_scripts)
    return errors


def _missing_required(error):
    """Return the property named by a JSON Schema ``required`` error."""
    message = error.message or ""
    marker = "' is a required property"
    if message.startswith("'") and marker in message:
        return message.split("'", 2)[1]
    return ""


def _schema_errors(canonical, errors):
    validator = Draft202012Validator(load_schema())
    for error in validator.iter_errors(canonical):
        if error.validator == "additionalProperties":
            continue
        pointer = _pointer(error.absolute_path)
        if error.validator == "required":
            missing = _missing_required(error)
            if missing:
                pointer = f"{pointer}/{missing}" if pointer else f"/{missing}"
        code = _SCHEMA_CODE.get(error.validator, "type")
        message = _schema_message(error, code)
        errors.append(FieldError(pointer, code, message, **_location(canonical, pointer)))


def _schema_message(error, code):
    if code == "required":
        missing = _missing_required(error)
        if missing == "key":
            return "Enter a key."
        if missing == "name":
            return "Enter a name."
        if missing == "type":
            return "Choose a type."
        return error.message
    if code == "pattern" and error.absolute_path and error.absolute_path[-1] == "key":
        return "Use letters, numbers, hyphens, or underscores."
    if code == "min_length":
        return "Enter a value."
    if code == "enum":
        return f"Unsupported value '{error.instance}'."
    if code == "max_length":
        return "Use fewer characters."
    return error.message


def _pointer(path):
    parts = []
    for part in path:
        text = str(part).replace("~", "~0").replace("/", "~1")
        parts.append(text)
    if not parts:
        return ""
    return "/" + "/".join(parts)


def _location(definition, pointer):
    section = "details"
    item = ""
    field_label = ""
    if pointer.startswith("/inputs"):
        section = "inputs"
        item = _item_name(definition, pointer, "inputs")
    elif pointer.startswith("/pre_actions") or pointer.startswith("/actions") or pointer.startswith("/post_actions"):
        section = "steps"
        head = pointer.split("/")[1]
        item = _item_name(definition, pointer, head)
    elif pointer.startswith("/schedule"):
        section = "schedule"
    if pointer:
        field_label = pointer.rstrip("/").split("/")[-1].replace("_", " ").title()
    return {"section": section, "item": item, "field_label": field_label}


def _item_name(definition, pointer, collection):
    parts = [part for part in pointer.split("/") if part]
    if len(parts) < 2 or not parts[1].isdigit():
        return ""
    items = definition.get(collection) or []
    index = int(parts[1])
    if index >= len(items) or not isinstance(items[index], dict):
        return ""
    return items[index].get("label") or items[index].get("name") or items[index].get("key") or ""


def _semantic(definition, errors, *, repo, enforce_policy, check_scripts):
    inputs = definition.get("inputs") or []
    if inputs and not isinstance(inputs, list):
        errors.append(FieldError("/inputs", "invalid_type", "Inputs must be a list.", section="inputs"))
        inputs = []
    keys = []
    seen = set()
    for index, item in enumerate(inputs):
        if not isinstance(item, dict):
            errors.append(
                FieldError(f"/inputs/{index}", "invalid_type", "Each input must be an object.", section="inputs")
            )
            continue
        key = item.get("key") or ""
        if key:
            if key in seen:
                errors.append(
                    FieldError(
                        f"/inputs/{index}/key",
                        "duplicate_key",
                        f"Duplicate input key '{key}'.",
                        section="inputs",
                        item=item.get("label") or key,
                        field_label="Key",
                    )
                )
            seen.add(key)
            keys.append(key)
        input_type = item.get("type")
        if input_type in ("choice", "multichoice"):
            choices = item.get("choices")
            if not isinstance(choices, str) and not choices:
                errors.append(
                    FieldError(
                        f"/inputs/{index}/choices",
                        "min_items",
                        "Add at least one option.",
                        section="inputs",
                        item=item.get("label") or key,
                        field_label="Options",
                    )
                )
            elif isinstance(choices, list) and "default" in item:
                values = [choice.get("value") if isinstance(choice, dict) else choice for choice in choices]
                if item.get("default") not in values:
                    errors.append(
                        FieldError(
                            f"/inputs/{index}/default",
                            "choice_default",
                            "Choose a default from the options.",
                            section="inputs",
                            item=item.get("label") or key,
                            field_label="Default",
                        )
                    )
        if input_type in ("object", "multiobject") and not item.get("model"):
            errors.append(
                FieldError(
                    f"/inputs/{index}/model",
                    "model_required",
                    "Choose a model.",
                    section="inputs",
                    item=item.get("label") or key,
                    field_label="Model",
                )
            )
    _references(inputs, errors)
    _actions(definition, errors, repo=repo, check_scripts=check_scripts)
    _templates(definition, errors)
    _schedule(definition, errors)
    if enforce_policy:
        from .policy import builder_policy_errors

        errors.extend(builder_policy_errors(definition))


def _references(inputs, errors):
    keyset = {item.get("key") for item in inputs if isinstance(item, dict) and item.get("key")}
    graph = {key: [] for key in keyset}
    for item in inputs:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        deps = list(item.get("depends_on") or [])
        show_if = item.get("show_if") if isinstance(item.get("show_if"), dict) else {}
        deps.extend(show_if.keys())
        for dep in deps:
            if dep == key:
                errors.append(
                    FieldError(
                        "/inputs",
                        "self_reference",
                        f"Input '{key}' cannot reference itself.",
                        section="inputs",
                        item=item.get("label") or key,
                    )
                )
                continue
            if dep not in keyset:
                errors.append(
                    FieldError(
                        "/inputs",
                        "unknown_reference",
                        f"Input '{key}' references unknown input '{dep}'.",
                        section="inputs",
                        item=item.get("label") or key,
                    )
                )
                continue
            graph[key].append(dep)
    visiting = set()
    visited = set()

    def walk(node, stack):
        if node in visiting:
            errors.append(
                FieldError(
                    "/inputs",
                    "cycle",
                    f"Inputs reference each other in a cycle: {' -> '.join(stack + [node])}.",
                    section="inputs",
                )
            )
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            walk(dep, stack + [node])
        visiting.remove(node)
        visited.add(node)

    for key in keyset:
        walk(key, [])


def _actions(definition, errors, *, repo, check_scripts):
    seen = {}
    for phase, key in PHASE_YAML_KEYS.items():
        actions = definition.get(key) or []
        if not isinstance(actions, list):
            errors.append(
                FieldError(f"/{key}", "invalid_type", "Each phase must be a list of actions.", section="steps")
            )
            continue
        for index, action in enumerate(actions):
            pointer = f"/{key}/{index}"
            if not isinstance(action, dict):
                errors.append(FieldError(pointer, "invalid_type", "Each action must be an object.", section="steps"))
                continue
            name = action.get("name") or ""
            action_type = action.get("type")
            if action_type not in ACTION_TYPES:
                errors.append(
                    FieldError(
                        f"{pointer}/type",
                        "enum",
                        "Action type must be nautobot or awx.",
                        section="steps",
                        item=name,
                        field_label="Type",
                    )
                )
                continue
            if name:
                if name in seen:
                    errors.append(
                        FieldError(
                            f"{pointer}/name",
                            "duplicate_action",
                            f"Duplicate action name '{name}'.",
                            section="steps",
                            item=name,
                            field_label="Name",
                        )
                    )
                else:
                    seen[name] = pointer
            when = action.get("when")
            if isinstance(when, str) and when and "{{" not in when and "{%" not in when:
                errors.append(
                    FieldError(
                        f"{pointer}/when",
                        "when_expression",
                        "Wrap the condition in `{{ }}`, for example `{{ inputs.confirm }}`",
                        section="steps",
                        item=name,
                        field_label="Condition",
                    )
                )
            if action_type == "nautobot":
                script = action.get("script") or ""
                if not script or ".." in str(script).split("/"):
                    errors.append(
                        FieldError(
                            f"{pointer}/script",
                            "script_required",
                            "Choose a script from the source repository.",
                            section="steps",
                            item=name,
                            field_label="Script",
                        )
                    )
                elif check_scripts:
                    if repo is None:
                        errors.append(
                            FieldError(
                                f"{pointer}/script",
                                "repository_required",
                                "Nautobot actions require a source Git repository",
                                section="steps",
                                item=name,
                                field_label="Script",
                            )
                        )
                    elif not _script_exists(repo, script):
                        errors.append(
                            FieldError(
                                f"{pointer}/script",
                                "script_missing",
                                f"Script not found in repository: {script}",
                                section="steps",
                                item=name,
                                field_label="Script",
                            )
                        )
                    elif enforce_path(script):
                        errors.append(
                            FieldError(
                                f"{pointer}/script",
                                "script_path",
                                "Choose a script from the scripts directory.",
                                section="steps",
                                item=name,
                                field_label="Script",
                            )
                        )
            else:
                template_id = action.get("template_id")
                if isinstance(template_id, bool) or not isinstance(template_id, int) or template_id <= 0:
                    errors.append(
                        FieldError(
                            f"{pointer}/template_id",
                            "template_required",
                            "Enter an AWX job template ID.",
                            section="steps",
                            item=name,
                            field_label="Job template ID",
                        )
                    )


def enforce_path(script):
    """Return True when a present script is outside ``scripts_dir``. Used while checking scripts."""
    return not script_path_allowed(script)


def _script_exists(repo, script):
    # Use the datasources global so sync unit tests can patch WorkflowHelper.
    from nautobot_workflow_engine import datasources

    return datasources.WorkflowHelper.objects.filter(source_repo=repo, file_path=script.lstrip("/")).exists()


def _templates(definition, errors):
    from jinja2 import TemplateSyntaxError
    from jinja2.exceptions import SecurityError

    from nautobot_workflow_engine.executors import get_jinja_environment

    env = get_jinja_environment()

    def walk(value, pointer):
        if isinstance(value, str):
            if "{{" not in value and "{%" not in value:
                return
            if len(value.encode("utf-8")) > TEMPLATE_SOURCE_LIMIT:
                errors.append(FieldError(pointer, "template_too_long", "Template is longer than 10 kB."))
                return
            try:
                env.from_string(value).render({})
            except SecurityError as exc:
                errors.append(FieldError(pointer, "template_rejected", f"Template was rejected: {exc}"))
            except TemplateSyntaxError as exc:
                # Repo filters such as normalize_for_dns are registered at run time.
                if "No filter named" in str(exc):
                    return
                errors.append(FieldError(pointer, "template_syntax", f"Template syntax is invalid: {exc}"))
            except Exception:
                return
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{pointer}/{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{pointer}/{index}")

    walk(definition, "")


def _schedule(definition, errors):
    schedule = definition.get("schedule")
    if not isinstance(schedule, dict):
        return
    servicenow = schedule.get("servicenow") if isinstance(schedule.get("servicenow"), dict) else {}
    change_key = servicenow.get("change_input_key")
    if change_key:
        keys = {item.get("key") for item in definition.get("inputs") or [] if isinstance(item, dict)}
        if change_key not in keys:
            errors.append(
                FieldError(
                    "/schedule/servicenow/change_input_key",
                    "change_input_missing",
                    "Choose an input that holds the change ticket.",
                    section="schedule",
                    field_label="Change ticket input",
                )
            )
    timezone_name = servicenow.get("timezone")
    if isinstance(timezone_name, str) and timezone_name and not _timezone_exists(timezone_name):
        errors.append(
            FieldError(
                "/schedule/servicenow/timezone",
                "timezone_invalid",
                "Choose a timezone from the list. Leave it empty to use UTC.",
                section="schedule",
                field_label="Timezone",
            )
        )


def _timezone_exists(name):
    return is_valid_timezone(name)


def reserved_key_errors(raw):
    """Reject a top-level alias that is set beside its canonical key, and preserved alias injection."""
    from . import PHASE_ALIASES

    if not isinstance(raw, dict):
        return []
    errors = []
    for alias, canonical in PHASE_ALIASES.items():
        if alias == canonical:
            continue
        if alias in raw and canonical in raw:
            errors.append(
                FieldError(
                    f"/{alias}",
                    "reserved_key",
                    f"'{alias}' is an alias of '{canonical}'. Use '{canonical}'.",
                    section="details",
                    field_label=alias,
                )
            )
    preserved = raw.get("preserved")
    if preserved in (None, "", {}):
        return errors
    if not isinstance(preserved, dict):
        errors.append(FieldError("/preserved", "invalid_type", "Preserved keys must be an object.", section="details"))
    return errors
