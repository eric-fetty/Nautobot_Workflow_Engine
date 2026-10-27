"""Find and rename Jinja references without touching string literals."""

from jinja2.nativetypes import NativeEnvironment

from . import PHASE_YAML_KEYS

_ENV = NativeEnvironment()


USER_ATTRIBUTES = ("id", "username", "email", "first_name", "last_name", "full_name", "groups")


def find_references(definition):
    """Return the inputs, earlier step results, and user fields a Jinja helper can offer."""
    if not isinstance(definition, dict):
        return {"inputs": [], "results": [], "user": _user_reference()}
    attributes = {}
    for template in _templates(definition):
        for kind, name, attr in _refs_in_template(template):
            attributes.setdefault((kind, name), [])
            if attr and attr not in attributes[(kind, name)]:
                attributes[(kind, name)].append(attr)
    inputs = []
    for item in definition.get("inputs") or []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        inputs.append(
            {
                "key": item["key"],
                "label": item.get("label") or item["key"],
                "type": item.get("type") or "string",
                "attributes": attributes.get(("inputs", item["key"]), []),
            }
        )
    results = []
    for phase, key in PHASE_YAML_KEYS.items():
        for index, action in enumerate(definition.get(key) or []):
            if not isinstance(action, dict) or not action.get("name"):
                continue
            results.append(
                {
                    "name": action["name"],
                    "phase": phase,
                    "order": index + 1,
                    "type": action.get("type") or "",
                    "attributes": attributes.get(("results", action["name"]), []),
                }
            )
    return {"inputs": inputs, "results": results, "user": _user_reference()}


def _user_reference():
    """Fields the sandboxed user object exposes. The helper autocompletes these."""
    return {"key": "user", "label": "Current user", "attributes": list(USER_ATTRIBUTES)}


def rename_reference(definition, op, old, new):
    """Rename an input key or an action name, including references in templates.

    Returns ``(definition, changes)``. ``changes`` lists ``pointer``, ``before``, and ``after``.
    """
    if not isinstance(definition, dict):
        return definition, []
    updated = _clone(definition)
    changes = []
    if op == "rename_input":
        _rename_input(updated, old, new, changes)
    elif op == "rename_action":
        _rename_action(updated, old, new, changes)
    kind = "inputs" if op == "rename_input" else "results"
    _rename_in_tree(updated, "", kind, old, new, changes)
    return updated, changes


def _rename_input(definition, old, new, changes):
    for index, item in enumerate(definition.get("inputs") or []):
        if not isinstance(item, dict):
            continue
        if item.get("key") == old:
            changes.append({"pointer": f"/inputs/{index}/key", "before": old, "after": new})
            item["key"] = new
        depends = item.get("depends_on")
        if isinstance(depends, list) and old in depends:
            item["depends_on"] = [new if part == old else part for part in depends]
            changes.append({"pointer": f"/inputs/{index}/depends_on", "before": old, "after": new})
        show_if = item.get("show_if")
        if isinstance(show_if, dict) and old in show_if:
            value = show_if.pop(old)
            show_if[new] = value
            changes.append({"pointer": f"/inputs/{index}/show_if", "before": old, "after": new})


def _rename_action(definition, old, new, changes):
    for key in PHASE_YAML_KEYS.values():
        for index, action in enumerate(definition.get(key) or []):
            if isinstance(action, dict) and action.get("name") == old:
                changes.append({"pointer": f"/{key}/{index}/name", "before": old, "after": new})
                action["name"] = new


def _rename_in_tree(value, pointer, kind, old, new, changes):
    if isinstance(value, str):
        updated = _rename_in_template(value, kind, old, new)
        return updated
    if isinstance(value, dict):
        for key, item in list(value.items()):
            child = f"{pointer}/{key}"
            replaced = _rename_in_tree(item, child, kind, old, new, changes)
            if isinstance(item, str) and replaced != item:
                value[key] = replaced
                changes.append({"pointer": child, "before": item, "after": replaced})
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{pointer}/{index}"
            replaced = _rename_in_tree(item, child, kind, old, new, changes)
            if isinstance(item, str) and replaced != item:
                value[index] = replaced
                changes.append({"pointer": child, "before": item, "after": replaced})
        return value
    return value


def _rename_in_template(source, kind, old, new):
    """Replace ``kind.old`` name tokens. String literals are separate tokens and are left alone.

    Token values do not include the spaces between them, so replacements are applied
    to the original source at the spans the lexer walks.
    """
    if f"{kind}.{old}" not in source:
        return source
    try:
        tokens = list(_ENV.lexer.tokenize(source))
    except Exception:
        return source
    spans = []
    pos = 0
    for token in tokens:
        idx = source.find(token.value, pos)
        if idx < 0:
            return source
        spans.append((token, idx, idx + len(token.value)))
        pos = idx + len(token.value)
    pieces = []
    cursor = 0
    for index, (token, start, end) in enumerate(spans):
        previous = spans[index - 1][0] if index else None
        before = spans[index - 2][0] if index > 1 else None
        if (
            token.type == "name"
            and token.value == old
            and previous is not None
            and previous.type == "dot"
            and before is not None
            and before.type == "name"
            and before.value == kind
        ):
            pieces.append(source[cursor:start])
            pieces.append(new)
            cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces)


def _refs_in_template(source):
    if "inputs." not in source and "results." not in source:
        return
    try:
        stream = _ENV.lexer.tokenize(source)
    except Exception:
        return
    window = []
    for token in stream:
        window.append(token)
        if len(window) > 5:
            window.pop(0)
        if len(window) >= 3 and window[-2].type == "dot" and window[-3].type == "name" and window[-1].type == "name":
            kind = window[-3].value
            if kind not in ("inputs", "results"):
                continue
            name = window[-1].value
            attr = ""
            yield kind, name, attr
        if (
            len(window) >= 5
            and window[-1].type == "name"
            and window[-2].type == "dot"
            and window[-3].type == "name"
            and window[-4].type == "dot"
            and window[-5].type == "name"
            and window[-5].value in ("inputs", "results")
        ):
            yield window[-5].value, window[-3].value, window[-1].value


def _templates(value):
    found = []

    def walk(item):
        if isinstance(item, str):
            if "{{" in item or "{%" in item:
                found.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _clone(value):
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value
