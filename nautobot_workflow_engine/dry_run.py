"""Render a workflow's conditions and parameters without executing it."""

import json
import logging
import threading

from django.contrib.contenttypes.models import ContentType
from jinja2 import TemplateError

from .executors import _render, get_jinja_environment
from .forms import DynamicWorkflowForm
from .models import Workflow, WorkflowInput

logger = logging.getLogger(__name__)

TRUTHY = {"true", "1", "yes", "on", "y"}
DEFAULT_TIMEOUT = 5
DEFAULT_OUTPUT_CAP = 1_000_000
PHASES = (("pre", "pre_actions"), ("main", "actions"), ("post", "post_actions"))


class DryRunTimeout(Exception):
    """The preview run exceeded its wall-clock limit."""


def dry_run(
    definition, inputs, mock_results, user, repo=None, *, timeout=DEFAULT_TIMEOUT, max_output=DEFAULT_OUTPUT_CAP
):
    """Clean the launch values and render each step. Nothing is executed or saved.

    ``timeout`` is seconds. ``max_output`` is the UTF-8 size of one rendered value.
    """
    definition = definition if isinstance(definition, dict) else {}
    submitted = inputs if isinstance(inputs, dict) else {}
    mocks = mock_results if isinstance(mock_results, dict) else {}
    rows = _input_rows(definition)
    form_errors, cleaned = _clean_form(rows, submitted)
    if form_errors:
        _log(definition, user)
        return {"form_errors": form_errors, "phases": [], "error": ""}
    resolve_errors, cleaned = _resolve_objects(user, rows, cleaned)
    if resolve_errors:
        _log(definition, user)
        return {"form_errors": resolve_errors, "phases": [], "error": ""}

    def render():
        return render_phases(definition, cleaned, mocks, user, repo=repo, max_output=max_output)

    try:
        phases = _within(render, timeout)
    except DryRunTimeout:
        _log(definition, user)
        return {
            "form_errors": [],
            "phases": [],
            "error": f"Preview run stopped after {timeout:g} seconds.",
        }
    _log(definition, user)
    return {"form_errors": [], "phases": phases, "error": ""}


def render_sample(template, inputs, user, *, results=None, max_output=100_000):
    """Render one template with sample inputs and results. Nothing is executed.

    The builder uses it for the live preview under a Jinja field and for computed options.
    """
    env = get_jinja_environment(strict=True)
    context = {
        "inputs": inputs if isinstance(inputs, dict) else {},
        "results": results if isinstance(results, dict) else {},
        "user": user,
    }
    try:
        rendered = _render(template, context, env=env)
    except (TemplateError, ValueError) as exc:
        return {"rendered": "", "error": str(exc)}
    text = rendered if isinstance(rendered, str) else json.dumps(rendered, default=str)
    if len(text.encode("utf-8")) > max_output:
        return {"rendered": "", "error": "Rendered output is larger than the preview cap."}
    return {"rendered": text, "error": ""}


def render_phases(definition, cleaned, mocks, user, *, repo=None, max_output=DEFAULT_OUTPUT_CAP):
    """Render every phase. Callers wrap this when they need the timeout."""
    env = get_jinja_environment(repo, strict=True)
    context = {"inputs": cleaned, "results": mocks, "user": user}
    phases = []
    for phase, key in PHASES:
        steps = []
        for action in definition.get(key) or []:
            if isinstance(action, dict):
                steps.append(_render_step(action, context, env, max_output))
        phases.append({"phase": phase, "steps": steps})
    return phases


def _render_step(action, context, env, max_output):
    name = action.get("name") or "step"
    raw_when = action.get("when") or ""
    had_when = bool(str(raw_when).strip())
    step = {"name": name, "when_rendered": "", "will_run": not had_when, "params": {}, "error": "", "skip_reason": ""}
    if had_when:
        rendered, error = _bounded(_render, raw_when, context, env, max_output)
        if error:
            step["error"] = error
            step["will_run"] = False
            return step
        step["when_rendered"] = "" if rendered is None else str(rendered)
        step["will_run"] = step["when_rendered"].strip().lower() in TRUTHY
        if not step["will_run"]:
            shown = step["when_rendered"] or "empty"
            step["skip_reason"] = f"rendered `{shown}` → skipped; only true/1/yes/on/y run"
            return step
    raw_params = action.get("extra_vars") if action.get("type") == "awx" else action.get("with")
    params, error = _bounded(_render, raw_params or {}, context, env, max_output)
    if error:
        step["error"] = error
        step["will_run"] = False
        return step
    step["params"] = (
        params if isinstance(params, (dict, list, str, int, float, bool)) or params is None else str(params)
    )
    return step


def _bounded(render, value, context, env, max_output):
    try:
        rendered = render(value, context, env=env)
    except (TemplateError, ValueError) as exc:
        return None, str(exc)
    try:
        size = len(json.dumps(rendered, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        size = len(str(rendered).encode("utf-8"))
    if size > max_output:
        return None, "Rendered output is larger than the 1 MB cap."
    return rendered, ""


def _within(func, timeout):
    outcome = {}

    def target():
        try:
            outcome["value"] = func()
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise DryRunTimeout()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _clean_form(rows, submitted):
    workflow = Workflow()
    data = {}
    for row in rows:
        value = submitted.get(row.key)
        if row.input_type == "boolean":
            data[row.key] = bool(value)
        elif value is not None:
            data[row.key] = value
    form = DynamicWorkflowForm(workflow, data, inputs=rows)
    for row in rows:
        if row.required and row.input_type != "boolean" and row.key in form.fields:
            form.fields[row.key].required = True
    errors = []
    for row in rows:
        value = submitted.get(row.key, None)
        missing = value is None or value == "" or value == []
        if row.required and missing:
            errors.append({"key": row.key, "message": "This field is required."})
    if errors:
        return errors, {}
    if not form.is_valid():
        for key, messages in form.errors.items():
            for message in messages:
                errors.append({"key": key, "message": str(message)})
        return errors, {}
    cleaned = dict(form.cleaned_data)
    for row in rows:
        if row.key not in cleaned and row.key in submitted:
            cleaned[row.key] = submitted[row.key]
    return [], cleaned


def _resolve_objects(user, rows, cleaned):
    errors = []
    resolved = dict(cleaned)
    for row in rows:
        if row.input_type not in ("object", "multiobject") or not row.content_type_id:
            continue
        model = row.content_type.model_class()
        if model is None:
            continue
        queryset = model.objects.all()
        restrict = getattr(queryset, "restrict", None)
        if callable(restrict):
            queryset = restrict(user, "view")
        value = cleaned.get(row.key)
        if value in (None, "", []):
            continue
        keys = value if isinstance(value, list) else [value]
        found = []
        for key in keys:
            obj = queryset.filter(pk=key).first()
            if obj is None:
                errors.append({"key": row.key, "message": "That object is not available."})
            else:
                found.append({"id": str(obj.pk), "name": str(obj)})
        if row.input_type == "object":
            resolved[row.key] = found[0] if found else value
        else:
            resolved[row.key] = found
    return errors, resolved


def _input_rows(definition):
    rows = []
    for index, item in enumerate(definition.get("inputs") or []):
        if not isinstance(item, dict):
            continue
        row = WorkflowInput(
            key=item.get("key") or f"field-{index}",
            label=item.get("label") or item.get("key") or "Field",
            input_type=item.get("type") or "string",
            required=bool(item.get("required")),
            default=item.get("default"),
            depends_on=item.get("depends_on") or [],
            choices=item.get("choices") if isinstance(item.get("choices"), list) else None,
            help_text=item.get("help_text") or "",
            placeholder=item.get("placeholder") or "",
            validators=item.get("validators"),
            visible_when=item.get("show_if"),
            columns=item.get("columns"),
        )
        row.content_type = _content_type(item.get("model"))
        rows.append(row)
    return rows


def _content_type(label):
    if not isinstance(label, str) or "." not in label:
        return None
    app_label, model_name = label.split(".", 1)
    return ContentType.objects.filter(app_label=app_label, model=model_name.lower()).first()


def _log(definition, user):
    logger.info(
        "Preview run for %s by user %s. No scripts or AWX jobs were started.",
        definition.get("key") or "",
        getattr(user, "pk", ""),
    )
