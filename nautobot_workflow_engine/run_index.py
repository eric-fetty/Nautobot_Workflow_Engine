"""Index each run's form values so run history can be searched and filtered by object.

Two things are kept per run:

* ``WorkflowRun.search_text``: the values typed or picked on the form (text, numbers,
  choices and their labels, list items, table cells) plus the names of picked objects.
  The run list's ``q`` search, and so Nautobot's global search, matches against it.
* ``WorkflowRunObjectReference`` rows: one per object an ``object`` or ``multiobject``
  input picked. Detail-page panels ("Workflow runs" on a device) and the
  ``referenced_object`` filter read these.

Inputs are dynamic, so the index is built from the run's stored values and the
workflow's input definitions at the time the run is saved. Values for inputs the
workflow no longer defines still go into the search text; objects that no longer exist
keep a reference with an empty name. ``build_run_index`` is pure so the backfill
migration can reuse it with historical models.
"""

import json
import logging
import uuid

logger = logging.getLogger(__name__)

OBJECT_INPUT_TYPES = ("object", "multiobject")
# A boolean's True/False says nothing useful to search for.
_SKIPPED_TEXT_TYPES = ("boolean",)
_CREATE_NEW = "__create__"
SEARCH_TEXT_MAX_LENGTH = 20000
DISPLAY_MAX_LENGTH = 255


def _as_uuid(value):
    if isinstance(value, dict):
        value = value.get("id") or value.get("pk")
    if value in (None, "", _CREATE_NEW):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def object_ids(value):
    """Return the object ids an object/multiobject value holds, in order, without duplicates."""
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = []
    items = value if isinstance(value, (list, tuple)) else [value]
    ids = []
    for item in items:
        parsed = _as_uuid(item)
        if parsed is not None and parsed not in ids:
            ids.append(parsed)
    return ids


def _scalars(value):
    """Yield the searchable scalar strings inside a stored input value."""
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        if text and text != _CREATE_NEW:
            yield text
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _scalars(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _scalars(item)


def _choice_labels(choices, value):
    """Yield the labels of the choices that ``value`` (one value or a list) selected."""
    if not isinstance(choices, list):
        return
    selected = {str(v) for v in (value if isinstance(value, (list, tuple)) else [value]) if v not in (None, "")}
    for choice in choices:
        if isinstance(choice, dict) and str(choice.get("value")) in selected:
            label = choice.get("label")
            if label not in (None, ""):
                yield str(label)


def build_run_index(inputs, input_defs, resolve_displays):
    """Return ``(references, search_text)`` for one run.

    Args:
        inputs: the run's stored ``inputs`` dict.
        input_defs: ``{key: {"input_type": str, "content_type_id": int | None, "choices": list | None}}``
            for the workflow's inputs. Keys missing here are treated as plain values.
        resolve_displays: ``callable(content_type_id, [uuid, ...]) -> {uuid: str}``. Ids it leaves out
            are objects that no longer exist (or can't be read); they keep an empty name.

    ``references`` is a list of dicts with ``input_key``, ``content_type_id``, ``object_id`` and
    ``object_display``.
    """
    inputs = inputs if isinstance(inputs, dict) else {}
    references = []
    words = []

    def add(text):
        if text and text not in words:
            words.append(text)

    for key, value in inputs.items():
        definition = input_defs.get(key) or {}
        input_type = definition.get("input_type")
        content_type_id = definition.get("content_type_id")
        if input_type in OBJECT_INPUT_TYPES:
            ids = object_ids(value)
            if not ids or not content_type_id:
                continue
            try:
                displays = resolve_displays(content_type_id, ids) or {}
            except Exception:  # noqa: BLE001 - history must still be recorded
                logger.debug("Could not resolve objects for input %s", key, exc_info=True)
                displays = {}
            for object_id in ids:
                display = str(displays.get(object_id) or "")[:DISPLAY_MAX_LENGTH]
                references.append(
                    {
                        "input_key": str(key)[:100],
                        "content_type_id": content_type_id,
                        "object_id": object_id,
                        "object_display": display,
                    }
                )
                add(display)
            continue
        if input_type in _SKIPPED_TEXT_TYPES:
            continue
        for text in _scalars(value):
            add(text)
        for label in _choice_labels(definition.get("choices"), value):
            add(label)

    return references, "\n".join(words)[:SEARCH_TEXT_MAX_LENGTH]


def input_definitions(input_queryset):
    """Return the ``input_defs`` mapping ``build_run_index`` expects from WorkflowInput rows."""
    return {
        wi.key: {"input_type": wi.input_type, "content_type_id": wi.content_type_id, "choices": wi.choices}
        for wi in input_queryset
    }


def display_resolver(get_model_for_content_type):
    """Return a ``resolve_displays`` callable that looks objects up through ``get_model_for_content_type``."""
    models_cache = {}

    def resolve(content_type_id, ids):
        if content_type_id not in models_cache:
            models_cache[content_type_id] = get_model_for_content_type(content_type_id)
        model = models_cache[content_type_id]
        if model is None:
            return {}
        from django.db import transaction

        # A savepoint, so a failed lookup can't poison the surrounding transaction.
        with transaction.atomic():
            found = list(model._default_manager.filter(pk__in=ids))
            return {obj.pk if isinstance(obj.pk, uuid.UUID) else _as_uuid(obj.pk): str(obj) for obj in found}

    return resolve


def _live_model_for_content_type(content_type_id):
    from django.contrib.contenttypes.models import ContentType

    try:
        return ContentType.objects.get_for_id(content_type_id).model_class()
    except ContentType.DoesNotExist:
        return None


def sync_run_index(workflow_run):
    """Rebuild one run's search text and object references from its stored inputs."""
    from django.db import transaction

    from .models import WorkflowRun, WorkflowRunObjectReference

    defs = input_definitions(workflow_run.workflow.inputs.all())
    references, text = build_run_index(workflow_run.inputs, defs, display_resolver(_live_model_for_content_type))
    with transaction.atomic():
        WorkflowRunObjectReference.objects.filter(workflow_run=workflow_run).delete()
        WorkflowRunObjectReference.objects.bulk_create(
            [WorkflowRunObjectReference(workflow_run=workflow_run, **ref) for ref in references],
            ignore_conflicts=True,
        )
        WorkflowRun.objects.filter(pk=workflow_run.pk).update(search_text=text)
    workflow_run.search_text = text
    return references, text


def inputs_fingerprint(instance):
    """Return a stable fingerprint of a loaded run's inputs, or None when inputs weren't loaded."""
    if "inputs" not in instance.__dict__:
        return None
    try:
        return json.dumps(instance.__dict__["inputs"], sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(instance.__dict__["inputs"])


def remember_inputs(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """post_init: note the inputs as loaded so post_save can tell whether they changed."""
    instance._nwe_inputs_fingerprint = inputs_fingerprint(instance)


def reindex_on_save(sender, instance, created, update_fields=None, raw=False, **kwargs):  # pylint: disable=unused-argument
    """post_save: rebuild the index when a run is created or its inputs change."""
    if raw:
        return
    if not created:
        if update_fields is not None and "inputs" not in update_fields:
            return
        current = inputs_fingerprint(instance)
        if current is not None and current == getattr(instance, "_nwe_inputs_fingerprint", None):
            return
    try:
        sync_run_index(instance)
    except Exception:  # noqa: BLE001 - indexing must never block a run
        logger.exception("Could not index workflow run %s for search", instance.pk)
    instance._nwe_inputs_fingerprint = inputs_fingerprint(instance)
