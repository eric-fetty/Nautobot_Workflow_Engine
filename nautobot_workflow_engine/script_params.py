"""Read the parameters a workflow script expects, without importing it.

A script can declare them in a ``PARAMETERS`` dict literal::

    PARAMETERS = {"device": {"type": "object", "model": "dcim.Device", "required": True, "help": "..."}}

Without that, the names are inferred from ``run(params)`` reading ``params["x"]``
(required) or ``params.get("x")`` (optional), or from keyword arguments of
``run(*, x, y=None)``. Only names and hints leave the server, never the source.
"""

import ast

PARAM_TYPES = {"string", "integer", "boolean", "object", "multiobject", "list", "choice", "dict"}


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _declared(tree):
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "PARAMETERS" for target in targets):
            continue
        data = _literal(value)
        if not isinstance(data, dict):
            return None
        rows = []
        for name, meta in data.items():
            if not isinstance(name, str):
                continue
            meta = meta if isinstance(meta, dict) else {}
            kind = meta.get("type") if meta.get("type") in PARAM_TYPES else ""
            row = {
                "name": name,
                "type": kind,
                "required": bool(meta.get("required", False)),
                "help": str(meta.get("help") or meta.get("description") or ""),
            }
            if "default" in meta and isinstance(meta["default"], (str, int, float, bool)):
                row["default"] = meta["default"]
            if isinstance(meta.get("model"), str):
                row["model"] = meta["model"]
            rows.append(row)
        return rows
    return None


def _run_function(tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            return node
    return None


def _param_name(node, holder):
    """Return the key when ``node`` is ``holder["key"]`` (True) or ``holder.get("key")`` (False)."""
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == holder
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value, True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == holder
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value, False
    return None, False


def _inferred(run):
    args = run.args
    if args.kwonlyargs:
        return [
            {"name": arg.arg, "type": "", "required": default is None, "help": ""}
            for arg, default in zip(args.kwonlyargs, args.kw_defaults)
        ]
    if not args.args:
        return []
    holder = args.args[0].arg
    hits = []
    for node in ast.walk(run):
        name, required = _param_name(node, holder)
        if name:
            hits.append((node.lineno, node.col_offset, name, required))
    order = []
    required_names = set()
    for _, _, name, required in sorted(hits):
        if name not in order:
            order.append(name)
        if required:
            required_names.add(name)
    return [{"name": name, "type": "", "required": name in required_names, "help": ""} for name in order]


def script_parameters(content):
    """Return ``{"declared": bool, "params": [...]}`` for a script's source text."""
    try:
        tree = ast.parse(content or "")
    except SyntaxError:
        return {"declared": False, "params": []}
    declared = _declared(tree)
    if declared is not None:
        return {"declared": True, "params": declared}
    run = _run_function(tree)
    if run is None:
        return {"declared": False, "params": []}
    return {"declared": False, "params": _inferred(run)}
