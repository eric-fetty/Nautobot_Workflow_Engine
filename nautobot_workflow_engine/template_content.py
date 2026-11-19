"""Detail-page content the Workflow Engine adds to other Nautobot object types.

A "Workflow runs" panel on each object type named in ``run_history_object_types``
(PLUGINS_CONFIG, default: devices and virtual machines) lists the latest runs whose
form picked that object. The panel works for any model because it reads the generic
``WorkflowRunObjectReference`` rows, so showing it on another page is a one-line
settings change.
"""

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.http import urlencode
from nautobot.apps.ui import Panel, SectionChoices, TemplateExtension

from .filters import runs_referencing
from .models import WorkflowRun
from .settings_helpers import run_history_object_types

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


template_extensions = [_extension_for(label) for label in run_history_object_types()]
