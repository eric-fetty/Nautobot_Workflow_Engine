"""Navigation menu configuration for the workflow engine plugin."""

from nautobot.apps.ui import NavMenuAddButton, NavMenuGroup, NavMenuItem, NavMenuTab

workflow_items = (
    NavMenuItem(
        link="plugins:nautobot_workflow_engine:workflows_list",
        name="Workflows",
        permissions=["nautobot_workflow_engine.view_workflow"],
    ),
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
    NavMenuItem(
        link="plugins:nautobot_workflow_engine:changefreeze_list",
        name="Change Freezes",
        permissions=["nautobot_workflow_engine.view_changefreeze"],
        buttons=(
            NavMenuAddButton(
                link="plugins:nautobot_workflow_engine:changefreeze_add",
                permissions=["nautobot_workflow_engine.add_changefreeze"],
            ),
        ),
    ),
)

menu_items = (
    NavMenuTab(
        name="Workflows",
        weight=50,
        icon="rocket-2",
        groups=(NavMenuGroup(name="Workflows", items=workflow_items),),
    ),
)
