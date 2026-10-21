"""Navigation menu configuration for the workflow engine plugin."""

from nautobot.apps.ui import NavMenuGroup, NavMenuItem, NavMenuTab

workflow_items = (
    NavMenuItem(
        link="plugins:nautobot_workflow_engine:workflows_list",
        name="Launch",
        permissions=["nautobot_workflow_engine.view_workflow"],
    ),
    NavMenuItem(
        link="plugins:nautobot_workflow_engine:workflow_builder_create",
        name="Builder",
        permissions=["nautobot_workflow_engine.change_workflow"],
    ),
)

reporting_items = (
    NavMenuItem(
        link="plugins:nautobot_workflow_engine:workflow_report",
        name="Dashboard",
        permissions=["nautobot_workflow_engine.view_workflowrun"],
    ),
    NavMenuItem(
        link="plugins:nautobot_workflow_engine:workflow_run_list",
        name="Run History",
        permissions=["nautobot_workflow_engine.view_workflowrun"],
    ),
)

menu_items = (
    NavMenuTab(
        name="Workflows",
        weight=50,
        icon="rocket-2",
        groups=(
            NavMenuGroup(name="Workflows", items=workflow_items),
            NavMenuGroup(name="Reporting", items=reporting_items),
        ),
    ),
)
