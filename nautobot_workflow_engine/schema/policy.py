"""Builder permissions, model allowlist, and the safe template subset.

Git sync does not call :func:`builder_policy_errors`. Repository YAML is reviewed
in Git. The builder API and the builder page do.
"""

import re

from . import PHASE_YAML_KEYS

_ATTR = r"[a-z_][a-z0-9_]{0,63}"
_INPUT_REF = rf"inputs\.{_ATTR}(?:\.{_ATTR}){{0,3}}"
_RESULT_REF = rf"results\.{_ATTR}(?:\.{_ATTR}){{0,3}}"
_REF = rf"(?:{_INPUT_REF}|{_RESULT_REF})"
_COND_ATOM = rf"(?:not\s+)?{_REF}(?:\s+(?:==|!=)\s+'(?:[^'\\]|\\.)*'|\s+in\s+\[[^\]]*\]|\s+is\s+none)?"
_SINGLE_REF_RE = re.compile(rf"^{{\{{\s*{_REF}\s*\}}}}$")
_CONDITION_RE = re.compile(rf"^{{\{{\s*{_COND_ATOM}(?:\s+(?:and|or)\s+{_COND_ATOM})*\s*\}}}}$")
AUTHOR_CODE_MESSAGE = "You need the *Build workflows* permission to change this field."


def can_build(user, workflow=None):
    """True when ``user`` may edit this workflow's draft, code fields and YAML included.

    Templates, conditions, script paths and the YAML view used to need a separate
    "author_code" action. They are now part of building: ``change`` on the workflow,
    or ``add`` for a workflow that doesn't exist yet.
    """
    if workflow is None or getattr(workflow, "pk", None) is None:
        return user.has_perm("nautobot_workflow_engine.add_workflow") or user.has_perm(
            "nautobot_workflow_engine.change_workflow"
        )
    return user.has_perm("nautobot_workflow_engine.change_workflow", workflow)


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
    if not isinstance(item, dict):
        return None
    raw_filter = item.get("filter")
    if isinstance(raw_filter, dict):
        return raw_filter.get("query")
    return None


def _choices_need_author(old, new):
    if old == new:
        return False
    return isinstance(old, str) or isinstance(new, str)


def _actions_by_name(definition):
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

    These fields are the schema properties marked ``x-ui.code``. A script path
    change always counts. Labels, order, and unchanged templates do not.
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
        if item.get("show_if") != previous.get("show_if") and not is_safe_template_value(item.get("show_if")):
            changes.append(f"{pointer}/show_if")
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
    label = label.lower()
    pattern = pattern.lower()
    if pattern.endswith("*"):
        return label.startswith(pattern[:-1])
    return label == pattern


def _model_label(label):
    text = str(label or "").strip()
    if "." not in text:
        return ""
    app, model = text.split(".", 1)
    return f"{app}.{model}".lower()


def content_type_allowed(label):
    """Return True when a model may appear in the builder. The denylist wins."""
    normalized = _model_label(label)
    if not normalized:
        return False
    for pattern in BUILDER_MODEL_DENYLIST:
        if _pattern_match(normalized, pattern):
            return False
    from ..settings_helpers import get_setting

    configured = get_setting("builder_model_allowlist")
    allowlist = configured if isinstance(configured, (list, tuple)) and configured else DEFAULT_MODEL_ALLOWLIST
    return any(_pattern_match(normalized, pattern) for pattern in allowlist)


def script_path_allowed(path):
    """Return True when a script path stays inside the configured scripts directory."""
    from ..settings_helpers import get_setting

    configured = get_setting("scripts_dir", "scripts")
    scripts_dir = str(configured or "scripts").strip().strip("/")
    normalized = str(path or "").strip().lstrip("/")
    if not normalized or not scripts_dir:
        return False
    if ".." in normalized.split("/"):
        return False
    return normalized == scripts_dir or normalized.startswith(scripts_dir + "/")


def _lookup_field(model, name):
    try:
        return model._meta.get_field(name)
    except Exception:
        for field in model._meta.get_fields():
            if getattr(field, "attname", None) == name:
                return field
    return None


def lookup_allowed(model, lookup):
    """Return True when a filter lookup stays on allowlisted models, at most three hops."""
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


def _model_for_label(label):
    from django.apps import apps

    normalized = _model_label(label)
    if "." not in normalized:
        return None
    app, model = normalized.split(".", 1)
    try:
        return apps.get_model(app, model)
    except LookupError:
        return None


def builder_policy_errors(definition):
    """Return field errors for models and scripts the builder is not allowed to save."""
    from .errors import FieldError

    errors = []
    if not isinstance(definition, dict):
        return errors
    for index, item in enumerate(definition.get("inputs") or []):
        if not isinstance(item, dict):
            continue
        model_label = item.get("model")
        if model_label and not content_type_allowed(model_label):
            errors.append(
                FieldError(
                    f"/inputs/{index}/model",
                    "model_denied",
                    "That model is not available in the builder.",
                    section="inputs",
                    item=item.get("label") or item.get("key") or "",
                    field_label="Model",
                )
            )
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
                    FieldError(
                        f"/inputs/{index}/filter/query",
                        "lookup_denied",
                        f"Filter lookup '{lookup}' is not allowed.",
                        section="inputs",
                        item=item.get("label") or item.get("key") or "",
                        field_label="Filter",
                    )
                )
    for phase, key in PHASE_YAML_KEYS.items():
        for index, action in enumerate(definition.get(key) or []):
            if not isinstance(action, dict):
                continue
            script = action.get("script")
            if script and not script_path_allowed(script):
                errors.append(
                    FieldError(
                        f"/{key}/{index}/script",
                        "script_path",
                        "Choose a script from the scripts directory.",
                        section="steps",
                        item=action.get("name") or "",
                        field_label="Script",
                    )
                )
    return errors
