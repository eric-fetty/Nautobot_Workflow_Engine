"""URL configuration for the workflow engine plugin."""

from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView
from nautobot.apps.urls import NautobotUIViewSetRouter

from . import views

app_name = "nautobot_workflow_engine"

# Create router and register WorkflowUIViewSet
router = NautobotUIViewSetRouter()
router.register("workflow", views.WorkflowUIViewSet)

urlpatterns = [
    # Old address of the Settings page, kept so bookmarks still work.
    path("config/", RedirectView.as_view(pattern_name="plugins:nautobot_workflow_engine:config"), name="config_legacy"),
    path("workflows_list/", views.WorkflowListView.as_view(), name="workflows_list"),
    path("workflows/builder/", views.WorkflowBuilderView.as_view(), name="workflow_builder_create"),
    path("workflows/builder/yaml/", views.WorkflowBuilderYamlView.as_view(), name="workflow_builder_yaml"),
    path("workflows/<slug:key>/builder/", views.WorkflowBuilderView.as_view(), name="workflow_builder_edit"),
    path(
        "workflows/<slug:key>/builder/duplicate/",
        views.WorkflowDuplicateView.as_view(),
        name="workflow_builder_duplicate",
    ),
    path("workflows/<slug:key>/launch/", views.WorkflowRunCreateView.as_view(), name="workflow_launch"),
    path("workflows/<slug:key>/inputs/<slug:child>/options/", views.InputOptionsView.as_view(), name="input_options"),
    # Nautobot's standard list-route name for WorkflowRun comes first, so resolving /runs/ gives it.
    # Core maps that name to the model for the header search scope ("in: Run History"), live search
    # and global search. The older name stays for existing links and reverse() calls.
    path("runs/", views.WorkflowRunListView.as_view(), name="workflowrun_list"),
    path("runs/", views.WorkflowRunListView.as_view(), name="workflow_run_list"),
    path("runs/<uuid:pk>/", views.WorkflowRunDetailView.as_view(), name="workflow_run"),
    path("runs/<uuid:pk>/progress/", views.WorkflowRunProgressView.as_view(), name="workflow_run_progress"),
    path("runs/<uuid:pk>/cancel/", views.WorkflowRunCancelView.as_view(), name="workflow_run_cancel"),
    path("settings/", views.WorkflowEngineSettingsView.as_view(), name="config"),
    path("reports/", views.WorkflowReportView.as_view(), name="workflow_report"),
    path("docs/", RedirectView.as_view(url=static("nautobot_workflow_engine/docs/index.html")), name="docs"),
    path("", include(router.urls)),
]
