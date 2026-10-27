"""Starter scripts written from the builder.

The builder never runs, imports, or evaluates this code. It is only parsed with
:func:`ast.parse` to check it. A new script is a starter: unless the person writes
their own body, ``run()`` prints each parameter it gets, one line per parameter, and
returns the names it received. Printed lines appear in the run's log, because the runner sends a
script's stdout there.
"""

import ast
import json
import keyword
import re

from nautobot_workflow_engine.script_params import PARAM_TYPES

SCRIPT_PERMISSION = "nautobot_workflow_engine.write_script_workflow"
NO_PERMISSION_MESSAGE = (
    "You need the “Write scripts” permission to create scripts. Copy the code and send it to an admin instead."
)
STARTER_NOTE = (
    "Nothing runs in the builder. Without code of your own, the script is a starter that prints each value it "
    "gets to the run's log. Replace that with your logic in the repository."
)
MAX_BODY_CHARS = 20000
_ALLOWED_TOP = ("PARAMETERS",)


def scripts_dir():
    """The configured scripts directory, the same one discovery and the runner use."""
    from .settings_helpers import get_setting

    configured = get_setting("scripts_dir", "scripts")
    return str(configured or "scripts").strip().strip("/") or "scripts"


def script_slug(name):
    """``"Check reachability"`` → ``"check_reachability"``. Empty when nothing usable is left."""
    text = re.sub(r"[^0-9a-zA-Z]+", "_", str(name or "").strip().lower()).strip("_")
    text = re.sub(r"_+", "_", text)
    if text.endswith("_py"):
        text = text[:-3].rstrip("_")
    if text and text[0].isdigit():
        text = f"script_{text}"
    if keyword.iskeyword(text):
        text = f"{text}_script"
    return text[:80]


def script_path(name):
    """Repository path for a new script, inside the scripts directory."""
    slug = script_slug(name)
    return f"{scripts_dir()}/{slug}.py" if slug else ""


def parameters_spec(rows):
    """Turn builder rows into a ``PARAMETERS`` dict. Bad rows are skipped, never guessed into code."""
    spec = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name.isidentifier() or keyword.iskeyword(name) or name in spec:
            continue
        kind = row.get("type") if row.get("type") in PARAM_TYPES else "string"
        entry = {"type": kind, "required": bool(row.get("required", True))}
        model = row.get("model")
        if kind in ("object", "multiobject") and isinstance(model, str) and re.fullmatch(r"\w+\.\w+", model):
            entry["model"] = model
        help_text = str(row.get("help") or "").strip()
        if help_text:
            entry["help"] = help_text[:200]
        spec[name] = entry
    return spec


def default_body(spec):
    """A starter body: print every value the step passes, one line per parameter.

    ``print()`` output goes to the run's log, so a new user sees exactly what arrived.
    """
    lines = ["# Replace this with your logic."]
    for name in spec:
        lines.append(f"print(f\"{name}: {{params.get('{name}')!r}}\")")
    if not spec:
        lines.append("for name, value in params.items():")
        lines.append('    print(f"{name}: {value!r}")')
    lines.append('return {"received": sorted(params)}')
    return "\n".join(lines) + "\n"


def render_script(*, path, title, spec, body=None):
    """The whole file: a docstring, ``PARAMETERS`` and ``run(params)``."""
    title = re.sub(r"\s+", " ", str(title or "").strip()) or path
    title = title.replace('"""', "'''")[:120]
    text = (body if body is not None and str(body).strip() else default_body(spec)).replace("\t", "    ")
    lines = text.rstrip("\n").splitlines() or ["pass"]
    indented = "\n".join(f"    {line}" if line.strip() else "" for line in lines)
    params = _literal(spec)
    return (
        f'"""{title}\n\nCreated in the workflow builder. Edit it in the repository.\n"""\n\n'
        f"PARAMETERS = {params}\n\n\n"
        "def run(params):\n"
        '    """Do the work for the workflow step. Return a dict for later steps to read."""\n'
        f"{indented}\n"
    )


def _py(value):
    """A Python literal with double quotes, like the example scripts."""
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_py(key)}: {_py(item)}" for key, item in value.items()) + "}"
    return json.dumps(str(value), ensure_ascii=False)


def _literal(spec):
    if not spec:
        return "{}"
    rows = "".join(f"    {_py(name)}: {_py(entry)},\n" for name, entry in spec.items())
    return "{\n" + rows + "}"


def check_script(text):
    """Parse ``text`` (never run it) and return plain problems, each ``{"line", "message"}``.

    Only a docstring, ``PARAMETERS`` and ``def run(params)`` may sit at the top level,
    so nothing in the file runs on import: code only runs when a workflow step calls run().
    """
    if len(text or "") > MAX_BODY_CHARS + 4000:
        return [{"line": 0, "message": f"The script is too long. Keep it under {MAX_BODY_CHARS} characters."}]
    try:
        tree = ast.parse(text or "", filename="script.py")
    except SyntaxError as exc:
        return [{"line": exc.lineno or 0, "message": f"Line {exc.lineno or '?'}: {exc.msg}."}]
    problems = []
    seen_run = False
    for index, node in enumerate(tree.body):
        line = getattr(node, "lineno", 0)
        if index == 0 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in _ALLOWED_TOP:
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):
                    problems.append({"line": line, "message": f"Line {line}: {name} must be a plain value."})
                    continue
                if name == "PARAMETERS" and not isinstance(value, dict):
                    problems.append({"line": line, "message": f"Line {line}: PARAMETERS must be a mapping."})
                continue
        if isinstance(node, ast.FunctionDef) and node.name == "run" and not seen_run:
            seen_run = True
            args = node.args
            plain = [arg.arg for arg in args.args] == ["params"] and not (
                args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults or node.decorator_list
            )
            if not plain:
                problems.append({"line": line, "message": f"Line {line}: keep the signature as def run(params):"})
            continue
        problems.append(
            {
                "line": line,
                "message": f"Line {line}: only the parameters and run(params) belong at the top. Put imports and other code inside run().",
            }
        )
    if not seen_run:
        problems.append({"line": 0, "message": "The script needs a run(params) function."})
    return problems
