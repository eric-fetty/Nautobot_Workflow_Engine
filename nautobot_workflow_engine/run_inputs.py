"""Present a run's stored form values for the run page.

Object and multi-object inputs are stored as ids. This turns them into the picked
objects' names, linked to their pages, using the run's ``WorkflowRunObjectReference``
rows for the type and the name saved when the run was recorded. Lookups are batched
per object type: one query for what the person may see and one for what still exists.

Each object entry has a ``state``:

* ``link``: the person may view the object; show its current name, linked.
* ``hidden``: the object exists but the person may not view it; show the saved name, no link.
* ``deleted``: the object is gone; show the saved name and a "deleted" marker, no link.
* ``raw``: nothing usable is known about the value; show it as stored.
"""

from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.utils.text import capfirst

from .run_index import OBJECT_INPUT_TYPES, _as_uuid

_CREATE_NEW = "__create__"


def _split_values(value):
    """Return the individual stored values of an object/multiobject input, in order."""
    import json

    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value]
    items = value if isinstance(value, (list, tuple)) else [value]
    return [item for item in items if item not in (None, "")]


def _raw_text(item):
    if isinstance(item, dict):
        return str(item.get("display") or item.get("name") or item.get("id") or item)
    return str(item)


def run_input_rows(workflow_run, user):
    """Return ``[{"key", "label", "is_object", "value", "objects"}]`` for the run's inputs."""
    inputs = workflow_run.inputs if isinstance(workflow_run.inputs, dict) else {}
    if not inputs:
        return []
    definitions = {}
    if workflow_run.workflow_id:
        definitions = {wi.key: wi for wi in workflow_run.workflow.inputs.all()}
    references = {}
    for ref in workflow_run.object_references.all():
        references[(ref.input_key, ref.object_id)] = ref

    # First pass: work out which ids of which type each object input holds.
    planned = []
    ids_by_type = defaultdict(set)
    for key, value in inputs.items():
        definition = definitions.get(key)
        input_type = getattr(definition, "input_type", None)
        is_object = input_type in OBJECT_INPUT_TYPES or any(ref_key == key for ref_key, _ in references)
        if not is_object:
            planned.append((key, definition, value, None))
            continue
        entries = []
        for item in _split_values(value):
            object_id = _as_uuid(item)
            ref = references.get((key, object_id)) if object_id else None
            content_type_id = ref.content_type_id if ref else getattr(definition, "content_type_id", None)
            if object_id and content_type_id:
                ids_by_type[content_type_id].add(object_id)
            entries.append((item, object_id, content_type_id, ref))
        planned.append((key, definition, value, entries))

    # Batched lookups, two queries per object type.
    visible = {}
    existing = {}
    types = {}
    for content_type_id, ids in ids_by_type.items():
        try:
            model = ContentType.objects.get_for_id(content_type_id).model_class()
        except ContentType.DoesNotExist:
            model = None
        types[content_type_id] = model
        if model is None:
            continue
        manager = model._default_manager
        queryset = manager.restrict(user, "view") if hasattr(manager.all(), "restrict") else manager.all()
        visible[content_type_id] = {obj.pk: obj for obj in queryset.filter(pk__in=ids)}
        existing[content_type_id] = set(manager.filter(pk__in=ids).values_list("pk", flat=True))

    rows = []
    for key, definition, value, entries in planned:
        row = {
            "key": key,
            "label": getattr(definition, "label", "") or key,
            "is_object": entries is not None,
            "value": value,
            "objects": [],
        }
        for item, object_id, content_type_id, ref in entries or []:
            row["objects"].append(_describe(item, object_id, content_type_id, ref, types, visible, existing))
        rows.append(row)
    return rows


def _describe(item, object_id, content_type_id, ref, types, visible, existing):
    model = types.get(content_type_id)
    saved_name = ref.object_display if ref else ""
    entry = {
        "state": "raw",
        "name": _raw_text(item),
        "url": "",
        "type_label": capfirst(model._meta.verbose_name) if model is not None else "",
    }
    if item == _CREATE_NEW:
        entry["name"] = "Create new"
        return entry
    if not object_id or model is None:
        return entry
    obj = visible.get(content_type_id, {}).get(object_id)
    if obj is not None:
        url = ""
        try:
            url = obj.get_absolute_url()
        except Exception:  # noqa: BLE001 - a model without a page still shows its name
            url = ""
        entry.update(state="link" if url else "hidden", name=str(obj), url=url)
        return entry
    if object_id in existing.get(content_type_id, set()):
        if saved_name:
            entry.update(state="hidden", name=saved_name)
        return entry
    if saved_name:
        entry.update(state="deleted", name=saved_name)
    return entry
