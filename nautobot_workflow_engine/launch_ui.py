"""Data for the launch page: field specs, the step summary, and run statistics.

The launch template ships this as JSON and ``launch.js`` draws the form from it.
The builder preview uses the same field spec shape, so both look the same.
Nothing here mentions Git, drafts, or versions: people running a workflow never
need to know about them.
"""

import json
import re
from datetime import timedelta
from statistics import median

from django.urls import reverse
from django.utils import timezone

# Material Design icons for the object types people pick most often.
MODEL_ICONS = {
    "dcim.location": "map-marker-outline",
    "dcim.device": "server",
    "dcim.interface": "ethernet",
    "dcim.rack": "server-network",
    "dcim.platform": "cog-outline",
    "ipam.vlan": "lan",
    "ipam.prefix": "ip-network-outline",
    "ipam.ipaddress": "ip-outline",
    "ipam.vrf": "router-network",
    "tenancy.tenant": "domain",
    "extras.tag": "tag-outline",
    "users.user": "account-outline",
    "circuits.circuit": "vector-polyline",
}

_META_ATTRS = ("description", "role", "location_type", "status", "platform")
_SIMPLE_WHEN = re.compile(r"^\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}$")
_COMPARE_WHEN = re.compile(r"^\{\{\s*inputs\.([A-Za-z0-9_]+)\s*(==|!=)\s*(.+?)\s*\}\}$")


def model_label(content_type):
    """Return ``app_label.model`` for a content type, or ''."""
    if content_type is None:
        return ""
    return f"{content_type.app_label}.{content_type.model}"


def option_meta(obj):
    """Return one short line under an option, such as 'Access switch · Active · Cisco IOS'."""
    parts = []
    for attr in _META_ATTRS:
        try:
            value = getattr(obj, attr, None)
        except Exception:  # noqa: BLE001 - a broken relation must not break the picker
            value = None
        if value in (None, ""):
            continue
        text = str(getattr(value, "name", None) or value).strip()
        if not text or text in parts or len(text) > 60:
            continue
        parts.append(text)
        if len(parts) == 3:
            break
    return " · ".join(parts)


def related_for_meta(model):
    """Foreign keys worth joining so option_meta does not query per row."""
    names = set()
    for field in model._meta.get_fields():
        if field.name in _META_ATTRS and getattr(field, "many_to_one", False):
            names.add(field.name)
    return sorted(names)


def option_label(obj):
    """Return the label people see for an object option."""
    for attr in ("display", "name", "label", "prefix", "address", "hostname"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    return str(obj)


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _range_sentence(low, high):
    if low is not None and high is not None:
        return f"Between {low} and {high}."
    if low is not None:
        return f"At least {low}."
    if high is not None:
        return f"At most {high}."
    return ""


def _has_jinja(value):
    text = json.dumps(value) if not isinstance(value, str) else value
    return "{{" in text or "{%" in text


def _show_if(visible_when):
    if isinstance(visible_when, dict) and visible_when:
        conditions = {}
        for key, value in visible_when.items():
            values = value if isinstance(value, list) else [value]
            conditions[str(key)] = [str(item).lower() if isinstance(item, bool) else str(item) for item in values]
        return {"kind": "map", "conditions": conditions}
    if isinstance(visible_when, str) and visible_when.strip():
        match = _SIMPLE_WHEN.match(visible_when.strip())
        if match:
            return {"kind": "truthy", "key": match.group(1)}
        return {"kind": "template"}
    return None


def _depends(value):
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part) for part in value if part not in (None, "")]
    return []


def field_spec(wi):
    """Describe one WorkflowInput for the launch form."""
    input_type = wi.input_type
    spec = {
        "key": wi.key,
        "label": wi.label or wi.key,
        "type": input_type,
        "required": bool(wi.required),
        "help": wi.help_text or "",
        "placeholder": getattr(wi, "placeholder", "") or "",
        "depends_on": _depends(wi.depends_on),
        "show_if": _show_if(wi.visible_when),
        "validators": wi.validators if isinstance(wi.validators, list) else [],
    }
    if input_type == "integer":
        spec["min_value"] = _number(wi.min_value)
        spec["max_value"] = _number(wi.max_value)
        sentence = _range_sentence(spec["min_value"], spec["max_value"])
        if sentence:
            spec["help"] = f"{spec['help']} {sentence}".strip() if spec["help"] else sentence
    if input_type in ("choice", "multichoice"):
        dynamic = _has_jinja(wi.choices) or bool(spec["depends_on"])
        spec["dynamic"] = dynamic
        spec["choices"] = (
            []
            if dynamic
            else [
                {"value": str(choice.get("value", "")), "label": str(choice.get("label") or choice.get("value", ""))}
                for choice in (wi.choices or [])
                if isinstance(choice, dict)
            ]
        )
        spec["nouns"] = "options"
        spec["noun"] = "option"
    if input_type in ("object", "multiobject"):
        spec["dynamic"] = True
        content_type = wi.content_type
        model = content_type.model_class() if content_type is not None else None
        label = model_label(content_type)
        spec["model"] = label
        spec["icon"] = MODEL_ICONS.get(label, "cube-outline")
        if model is not None:
            spec["noun"] = str(model._meta.verbose_name)
            spec["nouns"] = str(model._meta.verbose_name_plural)
    if input_type == "list":
        spec["min_items"] = wi.min_items
        spec["max_items"] = wi.max_items
    if input_type == "listdict":
        spec["columns"] = wi.columns if isinstance(wi.columns, list) else []
        spec["min_rows"] = wi.min_rows
        spec["max_rows"] = wi.max_rows
        spec["csv_import"] = getattr(wi, "csv_import", True) is not False
    return spec


def _parse_json(raw, fallback):
    if isinstance(raw, (list, dict)):
        return raw
    if not raw:
        return fallback
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return fallback
    return value


def submitted_value(wi, form):
    """Return the value to show for one input: what was posted, a prefill, or the default."""
    key = wi.key
    if form.is_bound:
        data = form.data
        if wi.input_type == "boolean":
            return str(data.get(key, "")).lower() in ("on", "true", "1", "yes")
        raw = data.get(key, "")
    else:
        if key in (form.initial or {}):
            raw = form.initial.get(key)
        else:
            raw = wi.default
        if wi.input_type == "boolean":
            return bool(raw)
    if wi.input_type in ("list", "listdict", "multiobject"):
        value = _parse_json(raw, [])
        return value if isinstance(value, list) else []
    if wi.input_type == "multichoice":
        if isinstance(raw, list):
            return [str(item) for item in raw]
        value = _parse_json(raw, [])
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)] if value not in (None, "") else []
    if isinstance(raw, dict):
        return str(raw.get("id") or raw.get("pk") or "")
    return "" if raw is None else str(raw)


def initial_labels(wi, value, user):
    """Resolve object ids to labels so a prefilled picker shows names, not UUIDs."""
    if wi.input_type not in ("object", "multiobject") or wi.content_type is None:
        return []
    ids = value if isinstance(value, list) else [value]
    ids = [str(item.get("id") if isinstance(item, dict) else item) for item in ids if item not in (None, "")]
    if not ids:
        return []
    model = wi.content_type.model_class()
    if model is None:
        return []
    try:
        queryset = model.objects.restrict(user, "view").filter(pk__in=ids)
        related = related_for_meta(model)
        if related:
            queryset = queryset.select_related(*related)
        return [{"value": str(obj.pk), "label": option_label(obj), "meta": option_meta(obj)} for obj in queryset]
    except Exception:  # noqa: BLE001 - a bad prefill should show an empty picker, not an error page
        return []


def _input_labels(definition):
    labels = {}
    choice_labels = {}
    for item in definition.get("inputs") or []:
        if isinstance(item, dict) and item.get("key"):
            labels[item["key"]] = item.get("label") or item["key"]
            choice_labels[item["key"]] = {
                str(choice.get("value")): choice.get("label") or choice.get("value")
                for choice in (item.get("choices") or [])
                if isinstance(choice, dict)
            }
    return labels, choice_labels


def when_sentence(when, labels, choice_labels):
    """Say a step condition in plain words."""
    text = str(when or "").strip()
    if not text:
        return ""
    match = _SIMPLE_WHEN.match(text)
    if match:
        return f"Only if “{labels.get(match.group(1), match.group(1))}” is Yes"
    match = _COMPARE_WHEN.match(text)
    if match:
        key, operator, raw = match.groups()
        raw = raw.strip().strip("\"'")
        shown = {"true": "Yes", "false": "No"}.get(raw.lower(), choice_labels.get(key, {}).get(raw, raw))
        verb = "is" if operator == "==" else "isn’t"
        return f"Only if “{labels.get(key, key)}” {verb} {shown}"
    return "Only if a condition is met"


def _step_row(action, phase, labels, choice_labels):
    label = action.get("label") or action.get("name") or "Step"
    action_type = action.get("type") or "nautobot"
    if phase == "pre":
        effect = "stops the run if it fails"
    elif phase == "post":
        effect = "runs even after a failure"
    elif action_type == "awx" and action.get("monitor", True):
        effect = "waits for the job to finish"
    else:
        effect = "continues on failure"
    condition = when_sentence(action.get("when"), labels, choice_labels)
    return {
        "title": label,
        "icon": "ansible" if action_type == "awx" else "script-text-outline",
        "sub": f"{condition} · {effect}" if condition else effect,
    }


def what_happens(workflow, field_count):
    """The step summary, read from the published definition."""
    definition = workflow.raw_definition if isinstance(workflow.raw_definition, dict) else {}
    labels, choice_labels = _input_labels(definition)
    rows = [
        {
            "kind": "step",
            "icon": "form-textbox",
            "title": "Someone fills in the form",
            "sub": f"{field_count} field{'' if field_count == 1 else 's'}",
        }
    ]
    if workflow.approval_required:
        rows.append(
            {
                "kind": "step",
                "icon": "account-check-outline",
                "title": "Waits for approval",
                "sub": "Each run waits until it’s approved",
            }
        )
    snow = workflow.servicenow_schedule_config
    if workflow.servicenow_change_input_key:
        states = [str(item) for item in snow.get("allowed_states") or [] if item]
        sub = f"Change must be {' or '.join(states)}" if states else "Runs inside the change’s window"
        rows.append({"kind": "step", "icon": "calendar-clock", "title": "Can wait for the change window", "sub": sub})
    for phase, key, heading in (
        ("pre", "pre_actions", "Before"),
        ("main", "actions", "Main"),
        ("post", "post_actions", "Always at the end"),
    ):
        actions = [item for item in definition.get(key) or [] if isinstance(item, dict)]
        if not actions:
            continue
        rows.append({"kind": "phase", "title": heading})
        rows.extend({"kind": "step", **_step_row(action, phase, labels, choice_labels)} for action in actions)
    manual = workflow.manual_duration_minutes
    done = f"Dashboard records ~{manual} min of manual effort, minus the run time" if manual else ""
    rows.append({"kind": "done", "icon": "flag-checkered", "title": "Done", "sub": done})
    return rows


def _humanize_minutes(seconds):
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes / 60
    return f"{hours:.1f} h".replace(".0 h", " h")


def _ago(when, now):
    delta = now - when
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} h ago"
    if delta < timedelta(days=2):
        return "yesterday"
    return f"{delta.days} days ago"


def about_rows(workflow):
    """Run facts for the About panel. Facts that aren't known are left out."""
    from .models import WorkflowRun

    now = timezone.now()
    rows = []
    finished = WorkflowRun.objects.filter(
        workflow=workflow, status="success", finished__isnull=False, started__gte=now - timedelta(days=90)
    ).values_list("started", "finished")[:200]
    durations = [(end - start).total_seconds() for start, end in finished if start and end and end >= start]
    if durations:
        rows.append(["Typically takes", _humanize_minutes(median(durations))])
    if workflow.manual_duration_minutes:
        rows.append(["By hand it takes", f"{workflow.manual_duration_minutes} min"])
    recent = WorkflowRun.objects.filter(workflow=workflow, started__gte=now - timedelta(days=30))
    total = recent.count()
    if total:
        success = recent.filter(status="success").count()
        rows.append(["Runs, last 30 days", f"{total} · {round(100 * success / total)}% succeeded"])
    last = WorkflowRun.objects.filter(workflow=workflow).order_by("-started").select_related("requested_by").first()
    if last is not None and last.started:
        who = getattr(last.requested_by, "username", "") if last.requested_by_id else ""
        rows.append(["Last run", f"{_ago(last.started, now)}{f' · {who}' if who else ''}"])
    return rows


def launch_config(request, workflow, form):
    """Everything launch.js needs to draw the form."""
    rows = list(workflow.inputs.all().order_by("order", "pk").select_related("content_type"))
    fields = []
    for wi in rows:
        if wi.key not in form.fields:
            continue
        spec = field_spec(wi)
        spec["value"] = submitted_value(wi, form)
        spec["labels"] = initial_labels(wi, spec["value"], request.user)
        fields.append(spec)
    errors = {}
    if form.is_bound:
        for name, messages in form.errors.items():
            errors[name] = [str(message) for message in messages]
    execution_choices = [
        {"value": "immediate", "label": "Run now", "help": "Starts as soon as you submit"},
        {"value": "scheduled", "label": "Schedule for later", "help": "Pick a date and time"},
    ]
    change_key = workflow.servicenow_change_input_key
    if change_key:
        change_label = next((wi.label for wi in rows if wi.key == change_key), change_key)
        execution_choices.append(
            {"value": "servicenow", "label": "In the change window", "help": f"Uses the window of “{change_label}”"}
        )
    execution = form.data.get("execution_type", "immediate") if form.is_bound else "immediate"
    return {
        "key": workflow.key,
        "fields": fields,
        "errors": errors,
        "options_url": reverse(
            "plugins:nautobot_workflow_engine:input_options", kwargs={"key": workflow.key, "child": "__CHILD__"}
        ),
        "execution": {
            "choices": execution_choices,
            "value": execution if execution in {choice["value"] for choice in execution_choices} else "immediate",
            "scheduled": form.data.get("scheduled_datetime", "") if form.is_bound else "",
        },
        "approval_required": bool(workflow.approval_required),
    }


def launch_context(request, workflow, form):
    """Template context for the launch page."""
    config = launch_config(request, workflow, form)
    return {
        "workflow": workflow,
        "form": form,
        "launch_config": config,
        "what_happens": what_happens(workflow, len(config["fields"])),
        "about_rows": about_rows(workflow),
        "non_field_errors": [str(error) for error in form.non_field_errors()] if form.is_bound else [],
    }
