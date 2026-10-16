"""App declaration for nautobot_workflow_engine."""

# Metadata is inherited from Nautobot. If not including Nautobot in the environment, this should be added
import re
from importlib import metadata
from pathlib import Path

from nautobot.apps import ConstanceConfigItem, NautobotAppConfig


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

_DEFAULT_SETTINGS = {
    "awx_external_integration_name": "AWX",
    "workflow_dir": "workflows",
    "filters_dir": "filters",
    "awx_poll_seconds": 5,
    "show_disabled": False,
    "show_add_new_item": [],
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
    default_settings = dict(_DEFAULT_SETTINGS)
    # Keys also listed in constance_config are not injected into PLUGINS_CONFIG,
    # so the Installed Apps configuration UI (and Constance) can own them at runtime.
    constance_config = {
        "workflow_dir": ConstanceConfigItem(
            default=_DEFAULT_SETTINGS["workflow_dir"],
            help_text="Directory containing workflow YAML in the workflow source.",
            field_type=str,
        ),
        "filters_dir": ConstanceConfigItem(
            default=_DEFAULT_SETTINGS["filters_dir"],
            help_text="Directory containing repository-backed Jinja filters.",
            field_type=str,
        ),
        "awx_external_integration_name": ConstanceConfigItem(
            default=_DEFAULT_SETTINGS["awx_external_integration_name"],
            help_text="Nautobot External Integration used for AWX actions.",
            field_type=str,
        ),
        "awx_poll_seconds": ConstanceConfigItem(
            default=_DEFAULT_SETTINGS["awx_poll_seconds"],
            help_text="Polling interval in seconds for AWX job monitoring.",
            field_type=int,
        ),
        "show_disabled": ConstanceConfigItem(
            default=_DEFAULT_SETTINGS["show_disabled"],
            help_text="Display disabled workflows in the workflow list.",
            field_type=bool,
        ),
        "show_add_new_item": ConstanceConfigItem(
            default=_DEFAULT_SETTINGS["show_add_new_item"],
            help_text=(
                "Object types (app_label.model) that offer the create-new-item choice "
                "on object inputs. Stored as a JSON list of strings."
            ),
            field_type="optional_json_field",
        ),
    }
    caching_config = {}
    config_view_name = "plugins:nautobot_workflow_engine:config"
    docs_view_name = "plugins:nautobot_workflow_engine:docs"
    searchable_models = ["workflow"]

    def ready(self):
        """Connect database-ready handlers after the app is fully loaded."""
        super().ready()
        from nautobot.apps import nautobot_database_ready

        from .signals import enable_required_jobs

        nautobot_database_ready.connect(enable_required_jobs, sender=self)


config = WorkflowEngineConfig  # pylint:disable=invalid-name
