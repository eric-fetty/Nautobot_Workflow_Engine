"""Turn visual-filter abbreviations into Django lookups.

The builder stores the short forms from the design (`field__n`, `field__ic`,
`field__isw`, and a list for "is one of"). Queries that already use Django
lookups, such as `name__icontains`, pass through unchanged.
"""

from nautobot_workflow_engine.schema.policy import LOOKUP_TRANSFORMS

_ALIAS = {"ic": "icontains", "isw": "istartswith"}


def split_filter(query):
    """Return `(include, exclude)` dicts safe to pass to `filter` and `exclude`."""
    include = {}
    exclude = {}
    if not isinstance(query, dict):
        return include, exclude
    for key, value in query.items():
        if not isinstance(key, str):
            continue
        if "{{" in key or "{%" in key:
            include[key] = value
            continue
        parts = key.split("__")
        suffix = parts[-1] if len(parts) > 1 else ""
        if suffix in ("n", "ne"):
            exclude["__".join(parts[:-1])] = value
            continue
        if suffix in _ALIAS:
            include["__".join(parts[:-1]) + "__" + _ALIAS[suffix]] = value
            continue
        if isinstance(value, list) and suffix not in LOOKUP_TRANSFORMS:
            include[f"{key}__in"] = value
            continue
        include[key] = value
    return include, exclude


def expanded_lookups(query):
    """Return the lookup names a policy check should see."""
    include, exclude = split_filter(query)
    return list(include) + list(exclude)
