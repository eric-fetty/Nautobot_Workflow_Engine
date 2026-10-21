"""App declaration for nautobot_workflow_engine."""

# Metadata is inherited from Nautobot. If not including Nautobot in the environment, this should be added
import os
import re
from importlib import metadata
from pathlib import Path

from nautobot.apps import NautobotAppConfig


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


class WorkflowEngineConfig(NautobotAppConfig):
    """App configuration for the nautobot_workflow_engine app."""

    name = "nautobot_workflow_engine"
    verbose_name = "Workflow Engine"
    version = __version__
    author = "Eric Fetty"
    description = "General-purpose workflow engine for Nautobot"
    base_url = "workflow-engine"
    required_settings = []
    default_settings = {
        "awx_external_integration_name": "AWX",
        "workflow_dir": "workflows",
        "filters_dir": "filters",
        "scripts_dir": "scripts",
        "builder_model_allowlist": [
            "dcim.*",
            "ipam.*",
            "circuits.*",
            "tenancy.*",
            "virtualization.*",
            "cloud.*",
            "wireless.*",
            "vpn.*",
            "load_balancers.*",
            "extras.tag",
            "extras.status",
            "extras.role",
            "extras.dynamicgroup",
        ],
        "awx_poll_seconds": 5,
        "show_disabled": False,
        "show_add_new_item": os.getenv("SHOW_ADD_NEW_ITEM", "").split(","),
    }
    caching_config = {}
    docs_view_name = "plugins:nautobot_workflow_engine:docs"
    searchable_models = ["workflow"]

    def ready(self):
        """Connect database-ready handlers after the app is fully loaded."""
        super().ready()
        from nautobot.apps import nautobot_database_ready

        from .signals import enable_required_jobs

        nautobot_database_ready.connect(enable_required_jobs, sender=self)


config = WorkflowEngineConfig  # pylint:disable=invalid-name
