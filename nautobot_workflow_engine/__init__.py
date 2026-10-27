"""App declaration for nautobot_workflow_engine."""

# Metadata is inherited from Nautobot. If not including Nautobot in the environment, this should be added
import re
from importlib import metadata
from pathlib import Path

from nautobot.apps import ConstanceConfigItem, NautobotAppConfig

from .settings_helpers import DEFAULTS as _SETTING_DEFAULTS
from .settings_helpers import SETTING_META as _SETTING_META


def _package_version():
    """Return the package version for Installed Apps and similar surfaces.

    Prefer ``pyproject.toml`` when running from a source checkout (including the
    Docker ``/source`` volume mount). Editable installs keep a stale
    ``*.dist-info`` Version until ``poetry install`` / image rebuild, which would
    otherwise show the wrong version in Nautobot's Installed Apps list.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(
            r'(?m)^version\s*=\s*["\']([^"\']+)["\']',
            pyproject.read_text(encoding="utf-8"),
        )
        if match:
            return match.group(1)
    return metadata.version(__name__)


__version__ = _package_version()

_CONSTANCE_FIELD_TYPES = {
    "show_disabled": bool,
    "awx_poll_seconds": int,
    "git_sync_interval_minutes": int,
    "git_remote_timeout_seconds": int,
    "git_push_lock_timeout": int,
    "show_add_new_item": "optional_json_field",
    "builder_model_allowlist": "optional_json_field",
    "git_push_retry_backoff_minutes": "optional_json_field",
}


class WorkflowEngineConfig(NautobotAppConfig):
    """App configuration for the nautobot_workflow_engine app."""

    name = "nautobot_workflow_engine"
    verbose_name = "Workflow Engine"
    version = __version__
    author = "Eric Fetty"
    description = "General-purpose workflow engine for Nautobot"
    base_url = "workflow-engine"
    required_settings = []
    # Every runtime setting is on the Settings page (Constance). Keys listed in
    # constance_config are not copied into PLUGINS_CONFIG, so setting one in
    # nautobot_config.py is an explicit override that locks it on the page.
    default_settings = {"allow_db_only_workflows": True}
    constance_config = {
        key: ConstanceConfigItem(
            default=_SETTING_DEFAULTS[key],
            help_text=_SETTING_META[key][1],
            field_type=_CONSTANCE_FIELD_TYPES.get(key, str),
        )
        for key in _SETTING_META
    }
    caching_config = {}
    config_view_name = "plugins:nautobot_workflow_engine:config"
    docs_view_name = "plugins:nautobot_workflow_engine:docs"
    searchable_models = ["workflow"]

    def ready(self):
        """Connect database-ready handlers after the app is fully loaded."""
        super().ready()
        from nautobot.apps import nautobot_database_ready

        from .default_permissions import ensure_default_permissions
        from .signals import enable_required_jobs

        nautobot_database_ready.connect(enable_required_jobs, sender=self)
        nautobot_database_ready.connect(ensure_default_permissions, sender=self)


config = WorkflowEngineConfig  # pylint:disable=invalid-name
