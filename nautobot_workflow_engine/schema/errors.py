"""Field errors shared by validation and the builder API."""


class FieldError(dict):
    """One JSON Pointer error.

    Shape: ``{"pointer", "code", "message", "section", "item", "field_label"}``.
    ``path`` is the legacy builder path the current page still renders.
    """

    def __init__(self, pointer, code, message, *, section="", item="", field_label="", path=None):
        """Store the pointer and a message a person can act on."""
        payload = {
            "pointer": pointer,
            "code": code,
            "message": message,
            "section": section or section_for(pointer),
            "item": item,
            "field_label": field_label,
            "path": path if path is not None else legacy_path(pointer),
        }
        super().__init__(payload)


_SECTION_BY_HEAD = {
    "pre_actions": "steps",
    "actions": "steps",
    "post_actions": "steps",
    "inputs": "inputs",
    "schedule": "schedule",
    "approval_required": "schedule",
    "name": "details",
    "key": "details",
    "description": "details",
    "category": "details",
    "manual_duration_minutes": "details",
    "source_repo": "details",
}


def section_for(pointer):
    """The builder section a pointer belongs to, so the issue shows next to the field."""
    head = str(pointer or "").lstrip("/").split("/", 1)[0]
    return _SECTION_BY_HEAD.get(head, "")


def legacy_path(pointer):
    """Turn an RFC 6901 pointer into the path the current builder page shows."""
    if not pointer:
        return ""
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
    if not parts:
        return ""
    head = parts[0]
    phase_keys = {"pre_actions": "pre", "actions": "main", "post_actions": "post"}
    if head in phase_keys and len(parts) >= 2 and str(parts[1]).isdigit():
        rest = parts[2:]
        path = f"actions.{phase_keys[head]}[{parts[1]}]"
        if rest:
            path += "." + ".".join(rest)
        return path
    if head == "schedule" and len(parts) >= 2 and parts[1] == "servicenow":
        rest = parts[2:]
        return "schedule." + ".".join(rest) if rest else "schedule"
    path = head
    for part in parts[1:]:
        if str(part).isdigit():
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


class BuilderValidationError(Exception):
    """Raised when a builder payload cannot be serialized.

    ``errors`` is a list of field errors the UI can render.
    """

    def __init__(self, errors):
        """Store field errors and a single summary message."""
        self.errors = [dict(item) for item in errors]
        summary = "; ".join(f"{item.get('path') or 'definition'}: {item.get('message')}" for item in self.errors)
        super().__init__(summary or "Workflow definition is invalid.")
