"""Detail-page content the Workflow Engine adds to other Nautobot object types.

A "Workflow runs" panel lists the latest runs whose form picked the object on the page.
It reads the generic ``WorkflowRunObjectReference`` rows, so it works for any model.

Nautobot registers detail-page extensions once, when the app loads, so one is registered
for every eligible object type (see ``run_history_eligible_models``). Whether the panel
shows is decided on each page view from the **Show “Workflow runs” on** setting
(default: devices and virtual machines), so changing it on the Settings page needs no
restart.
"""

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.http import urlencode
from nautobot.apps.ui import Panel, SectionChoices, TemplateExtension

from .filters import runs_referencing
from .models import WorkflowRun
from .settings_helpers import run_history_eligible_models, run_history_object_types

PANEL_RUN_LIMIT = 5
VIEW_RUNS_PERMISSION = "nautobot_workflow_engine.view_workflowrun"


class WorkflowRunsPanel(Panel):
    """Latest runs whose form picked the object on this page, limited to runs the viewer may see."""

    body_content_template_path = "nautobot_workflow_engine/inc/object_workflow_runs_panel.html"
    body_wrapper_template_path = "components/panel/body_wrapper_table.html"
    footer_content_template_path = "nautobot_workflow_engine/inc/object_workflow_runs_footer.html"
    header_extra_content_template_path = "nautobot_workflow_engine/inc/object_workflow_runs_header.html"

    def __init__(self, *, limit=PANEL_RUN_LIMIT, **kwargs):
        """Create the panel; ``limit`` caps how many runs are listed."""
        kwargs.setdefault("label", "Workflow runs")
        kwargs.setdefault("section", SectionChoices.RIGHT_HALF)
        kwargs.setdefault("weight", 450)
        kwargs.setdefault("required_permissions", [VIEW_RUNS_PERMISSION])
        super().__init__(**kwargs)
        self.limit = limit

    def should_render(self, context):
        """Show only on object types picked in the setting, and only to people who may view runs."""
        if not super().should_render(context):
            return False
        obj = context.get("object")
        if obj is None or getattr(obj, "pk", None) is None:
            return False
        meta = obj._meta
        return f"{meta.app_label}.{meta.model_name}" in run_history_object_types()

    def get_extra_context(self, context):
        """Add the runs, their total and the "View all" link for the page's object."""
        obj = context.get("object")
        request = context.get("request")
        user = getattr(request, "user", None)
        if obj is None or user is None or getattr(obj, "pk", None) is None:
            return {"workflow_runs": [], "workflow_runs_total": 0, "workflow_runs_url": ""}
        content_type = ContentType.objects.get_for_model(obj)
        runs = runs_referencing(
            WorkflowRun.objects.restrict(user, "view").select_related("workflow", "requested_by"),
            obj.pk,
            content_type,
        ).order_by("-started")
        total = runs.count()
        query = urlencode(
            {
                "referenced_object": str(obj.pk),
                "referenced_object_type": f"{content_type.app_label}.{content_type.model}",
            }
        )
        return {
            "workflow_runs": list(runs[: self.limit]) if total else [],
            "workflow_runs_total": total,
            "workflow_runs_more": max(total - self.limit, 0),
            "workflow_runs_url": f"{reverse('plugins:nautobot_workflow_engine:workflow_run_list')}?{query}",
        }


def _extension_for(model_label):
    """Build the TemplateExtension class that adds the panel to one model's detail page."""
    app_label, model_name = model_label.split(".")
    class_name = f"WorkflowRuns{app_label.title().replace('_', '')}{model_name.title().replace('_', '')}Extension"
    return type(
        class_name,
        (TemplateExtension,),
        {
            "model": model_label,
            "object_detail_panels": [WorkflowRunsPanel()],
            "__doc__": f"Workflow runs panel on {model_label} detail pages.",
            "__module__": __name__,
        },
    )


template_extensions = [
    _extension_for(f"{model._meta.app_label}.{model._meta.model_name}") for model in run_history_eligible_models()
]
