"""Template tags for workflow engine."""

import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="to_json")
def to_json(value):
    """Serialize a Python value to a JSON string (safe for embedding in <script> tags)."""
    try:
        return mark_safe(json.dumps(value))  # noqa: S308
    except (TypeError, ValueError):
        return "[]"


@register.simple_tag
def calculate_duration(start_time, end_time):
    """Calculate and format duration between two datetime objects."""
    if not start_time or not end_time:
        return "-"

    duration = end_time - start_time
    total_seconds = int(duration.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"
