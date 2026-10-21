"""Write ``docs/user/workflow_schema.md`` from ``workflow.schema.json``.

Run ``python -m nautobot_workflow_engine.schema.generate_docs`` and add ``--check``
in CI so the page cannot drift from the schema.
"""

import argparse
from pathlib import Path

from nautobot_workflow_engine.schema import SCHEMA_PATH, load_schema

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "user" / "workflow_schema.md"


def render_markdown(schema=None):
    """Return the schema reference page."""
    document = schema or load_schema()
    lines = [
        "# Workflow schema",
        "",
        "This page is generated from `nautobot_workflow_engine/schema/workflow.schema.json`.",
        "Do not edit it by hand. Run `invoke generate-schema-docs` after a schema change.",
        "",
        "The schema is the only list of input types, column types, action types, and phase aliases.",
        "Unknown keys are kept. `enabled` is operational state and is not part of the Git file.",
        "",
        "## Workflow",
        "",
        _properties_table(document.get("properties") or {}),
        "",
    ]
    for name, definition in (document.get("$defs") or {}).items():
        lines.append(f"## {definition.get('title') or name}")
        lines.append("")
        if definition.get("description"):
            lines.append(definition["description"])
            lines.append("")
        aliases = definition.get("x-aliases")
        if aliases:
            lines.append(f"Aliases: {', '.join(f'`{alias}`' for alias in aliases)}.")
            lines.append("")
        lines.append(_properties_table(definition.get("properties") or {}))
        lines.append("")
    lines.append("## Phase aliases")
    lines.append("")
    lines.append("| Canonical key | Aliases |")
    lines.append("| --- | --- |")
    for key, prop in (document.get("properties") or {}).items():
        aliases = prop.get("x-aliases")
        if aliases:
            lines.append(f"| `{key}` | {', '.join(f'`{alias}`' for alias in aliases)} |")
    lines.append("")
    return "\n".join(lines)


def _properties_table(properties):
    lines = [
        "| Field | Type | Label | Code | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not properties:
        lines.append("|  |  |  |  |  |")
        return "\n".join(lines)
    for name, prop in properties.items():
        if "$ref" in prop:
            field_type = f"ref `{prop['$ref'].rsplit('/', 1)[-1]}`"
            ui = prop.get("x-ui") or {}
        else:
            field_type = prop.get("type") or ("one of" if "oneOf" in prop else "")
            if prop.get("enum"):
                field_type = ", ".join(f"`{item}`" for item in prop["enum"])
            ui = prop.get("x-ui") or {}
        label = ui.get("label") or prop.get("title") or ""
        code = "yes" if ui.get("code") else ""
        description = (prop.get("description") or ui.get("help") or "").replace("\n", " ")
        aliases = prop.get("x-aliases")
        if aliases:
            description = (
                (description + " " if description else "") + "Aliases: " + ", ".join(f"`{item}`" for item in aliases)
            )
        lines.append(f"| `{name}` | {field_type} | {label} | {code} | {description} |")
    return "\n".join(lines)


def main(argv=None):
    """Write the page, or fail when ``--check`` finds drift."""
    parser = argparse.ArgumentParser(description="Generate the workflow schema reference.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when the page is stale.")
    args = parser.parse_args(argv)
    rendered = render_markdown()
    current = DOCS_PATH.read_text(encoding="utf-8") if DOCS_PATH.is_file() else ""
    if args.check:
        if current != rendered:
            raise SystemExit(f"{DOCS_PATH} is stale. Run invoke generate-schema-docs.")
        return
    DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {DOCS_PATH.relative_to(SCHEMA_PATH.parents[2])}")


if __name__ == "__main__":
    main()
