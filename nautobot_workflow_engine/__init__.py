"""App declaration for nautobot_workflow_engine."""

# Metadata is inherited from Nautobot. If not including Nautobot in the environment, this should be added
import os
from importlib import metadata

from nautobot.apps import NautobotAppConfig

__version__ = metadata.version(__name__)


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
        "awx_poll_seconds": 5,
        "show_disabled": False,
        "show_add_new_item": os.getenv("SHOW_ADD_NEW_ITEM", "").split(","),
    }
    caching_config = {}
    docs_view_name = "plugins:nautobot_workflow_engine:docs"
    searchable_models = ["workflow"]


config = WorkflowEngineConfig  # pylint:disable=invalid-name
