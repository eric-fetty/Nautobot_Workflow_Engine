"""Filterable fields for the visual filter builder.

The list comes from the model's Nautobot FilterSet. Status and Role are offered
as named choices. Foreign keys are offered as objects. Text and numbers stay typed.
"""

from nautobot.core.utils.lookup import get_filterset_for_model

_SKIP = {"q", "id", "created", "last_updated"}
_OPERATORS = {
    "text": ["is", "is not", "contains", "starts with", "is empty"],
    "number": ["is", "is not", "greater than", "less than", "is empty"],
    "choice": ["is", "is not", "is one of"],
    "object": ["is", "is not", "is one of", "is empty"],
    "boolean": ["is", "is not"],
}
_CHOICE_TYPES = {"StatusFilter", "RoleFilter", "ChoiceFilter", "MultipleChoiceFilter"}


def example_objects(model, user=None, limit=8):
    """Return a few objects so the filter builder can preview a dependency."""
    order = "name" if any(field.name == "name" for field in model._meta.fields) else "pk"
    queryset = model.objects.all()
    restrict = getattr(queryset, "restrict", None)
    if user is not None and callable(restrict):
        queryset = restrict(user, "view")
    rows = []
    for obj in queryset.order_by(order)[:limit]:
        rows.append({"id": str(obj.pk), "label": str(getattr(obj, "name", None) or obj)})
    return rows


def filter_fields_for_model(model, user=None):
    """Return filter rows the builder can show for ``model``."""
    filterset = get_filterset_for_model(model)
    if filterset is None:
        return []
    rows = []
    for name, filter_obj in filterset().filters.items():
        if name in _SKIP or name.startswith("has_"):
            continue
        kind = _kind(filter_obj)
        if kind is None:
            continue
        field_name = getattr(filter_obj, "field_name", None) or name
        if not _forward_field(model, field_name) and type(filter_obj).__name__ not in _CHOICE_TYPES:
            continue
        related = _related_model(filter_obj)
        choices = []
        if kind == "choice":
            choices = _choice_names(model, filter_obj, related, user)
        elif kind == "object":
            choices = _object_names(related, user)
        label = _clean_label(getattr(filter_obj, "label", None) or field_name)
        examples = example_objects(related, user, limit=8) if kind == "object" and related is not None else []
        rows.append(
            {
                "key": name,
                "label": label,
                "kind": kind,
                "operators": list(_OPERATORS[kind]),
                "choices": choices,
                "examples": examples,
                "lookup": "name" if related is not None and kind in ("object", "choice") else "",
                "related_model": _model_label(related) if kind == "object" else "",
            }
        )
    rows.sort(key=lambda row: row["label"].lower())
    return rows


def _kind(filter_obj):
    cls = type(filter_obj).__name__
    if cls in _CHOICE_TYPES:
        return "choice"
    if "Boolean" in cls:
        return "boolean"
    if "Number" in cls or "Decimal" in cls:
        return "number"
    if any(token in cls for token in ("ModelChoice", "ModelMultiple", "NaturalKey", "TreeNode")):
        return "object"
    if "Char" in cls or "MAC" in cls or cls == "SearchFilter":
        return "text"
    return None


def _forward_field(model, field_name):
    try:
        field = model._meta.get_field(field_name)
    except Exception:
        return False
    if not getattr(field, "is_relation", False):
        return True
    return bool(getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False))


def _related_model(filter_obj):
    queryset = getattr(filter_obj, "queryset", None)
    model = getattr(queryset, "model", None)
    if model is not None:
        return model
    field = getattr(filter_obj, "field", None)
    return getattr(field, "queryset", None) and field.queryset.model


def _choice_names(model, filter_obj, related, user):
    extra = getattr(filter_obj, "extra", None) or {}
    static = extra.get("choices")
    if static:
        return [str(label) for _value, label in list(static)[:80]]
    if related is None:
        return []
    manager = related.objects
    getter = getattr(manager, "get_for_model", None)
    queryset = getter(model) if callable(getter) else manager.all()
    return _names(queryset, user)


def _object_names(related, user):
    if related is None:
        return []
    return _names(related.objects.all(), user, limit=40)


def _names(queryset, user, limit=80):
    restrict = getattr(queryset, "restrict", None)
    if user is not None and callable(restrict):
        queryset = restrict(user, "view")
    names = []
    order = "name" if any(field.name == "name" for field in queryset.model._meta.fields) else "pk"
    for obj in queryset.order_by(order)[:limit]:
        names.append(str(getattr(obj, "name", None) or obj))
    return names


def _clean_label(label):
    text = str(label)
    for suffix in (" (name or ID)", " (slug or ID)", " (ID)"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text[:1].upper() + text[1:] if text else text


def _model_label(model):
    if model is None:
        return ""
    return f"{model._meta.app_label}.{model.__name__}"
