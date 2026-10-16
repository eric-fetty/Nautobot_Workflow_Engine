"""Helpers for reading and writing Workflow Engine app settings.

Values may come from ``PLUGINS_CONFIG`` in ``nautobot_config.py`` (deploy-time
override) or from django-constance (Installed Apps configuration UI). File
settings take precedence when a key is present in ``PLUGINS_CONFIG``.
"""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.config import get_app_settings_or_config

APP_NAME = "nautobot_workflow_engine"

# Content types from these apps are not useful as workflow object inputs.
_EXCLUDED_CONTENT_TYPE_APPS = frozenset(
    {
        "admin",
        "admin_tools",
        "contenttypes",
        "django_celery_beat",
        "django_celery_results",
        "health_check",
        "sessions",
        "social_django",
        "taggit",
    }
)

SETTING_META = {
    "workflow_dir": {
        "label": "Workflow directory",
        "help_text": "Directory containing workflow YAML in the workflow source.",
    },
    "filters_dir": {
        "label": "Filters directory",
        "help_text": "Directory containing repository-backed Jinja filters.",
    },
    "awx_external_integration_name": {
        "label": "AWX external integration",
        "help_text": "Nautobot External Integration used for AWX actions.",
    },
    "awx_poll_seconds": {
        "label": "AWX poll interval (seconds)",
        "help_text": "Polling interval in seconds for AWX job monitoring.",
    },
    "show_disabled": {
        "label": "Show disabled workflows",
        "help_text": "Display disabled workflows in the workflow list.",
    },
    "show_add_new_item": {
        "label": "Show add-new-item for models",
        "help_text": (
            'Object types that offer a "Create New" choice on object inputs. '
            "Select one or more models (stored as app_label.model)."
        ),
    },
}

SETTING_KEYS = tuple(SETTING_META.keys())

FILE_OVERRIDE_HELP = (
    "This setting is locked because it is set in PLUGINS_CONFIG "
    "(nautobot_config.py). Remove the key from the file configuration to edit it here."
)


def get_setting(key, fallback=None):
    """Return an app setting from PLUGINS_CONFIG or Constance."""
    return get_app_settings_or_config(APP_NAME, key, fallback=fallback)


def is_file_locked(key):
    """Return True when ``key`` is explicitly set in PLUGINS_CONFIG."""
    plugin_config = settings.PLUGINS_CONFIG.get(APP_NAME, {})
    return key in plugin_config


def constance_key(key):
    """Return the django-constance attribute name for an app setting key."""
    return f"{APP_NAME}__{key}"


def content_type_choice_label(content_type):
    """Return a human-readable label for a ContentType choice."""
    labeled = getattr(content_type, "app_labeled_name", None)
    if labeled:
        return labeled
    return f"{content_type.app_label} | {content_type.name}"


def content_type_setting_value(content_type):
    """Return the canonical stored value for a ContentType (``app_label.model``)."""
    return f"{content_type.app_label}.{content_type.model}"


def iter_object_content_type_choices():
    """Yield ``(value, label)`` pairs for ContentTypes usable as object inputs."""
    for content_type in ContentType.objects.order_by("app_label", "model"):
        if content_type.app_label in _EXCLUDED_CONTENT_TYPE_APPS:
            continue
        if content_type.model_class() is None:
            continue
        yield content_type_setting_value(content_type), content_type_choice_label(content_type)


def normalize_show_add_new_item_labels(value):
    """Normalize stored labels to canonical ``app_label.model`` strings.

    Accepts a list, or a comma-separated string. Legacy values that used
    ``app_label.<verbose_name>`` are rewritten to ``app_label.model`` when possible.
    """
    if value is None:
        raw_labels = []
    elif isinstance(value, str):
        raw_labels = [part.strip() for part in value.split(",") if part.strip()]
    else:
        raw_labels = [str(item).strip() for item in value if str(item).strip()]

    normalized = []
    seen = set()
    for label in raw_labels:
        canonical = _canonicalize_content_type_label(label)
        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(canonical)
    return normalized


def _canonicalize_content_type_label(label):
    """Resolve a single label to ``app_label.model`` when a ContentType is found."""
    if "." not in label:
        return label
    app_label, remainder = label.split(".", 1)
    app_label = app_label.strip()
    remainder = remainder.strip()
    if not app_label or not remainder:
        return label

    by_model = ContentType.objects.filter(app_label=app_label, model=remainder.lower()).first()
    if by_model is not None:
        return content_type_setting_value(by_model)

    # Legacy match against ContentType.name (verbose_name).
    for content_type in ContentType.objects.filter(app_label=app_label):
        if content_type.name.lower() == remainder.lower():
            return content_type_setting_value(content_type)

    return f"{app_label}.{remainder.lower()}"


def content_type_matches_add_new_item(content_type, configured_labels):
    """Return True when a ContentType is enabled for the create-new-item choice."""
    labels = {str(item).strip().lower() for item in (configured_labels or []) if str(item).strip()}
    if not labels:
        return False
    candidates = {
        content_type_setting_value(content_type).lower(),
        f"{content_type.app_label}.{content_type.name}".lower(),
    }
    return bool(candidates & labels)
