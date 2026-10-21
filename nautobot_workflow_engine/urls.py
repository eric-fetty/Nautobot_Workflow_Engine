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
    path("workflows_list/", views.WorkflowListView.as_view(), name="workflows_list"),
    path("workflows/builder/", views.WorkflowBuilderView.as_view(), name="workflow_builder_create"),
    path("workflows/builder/yaml/", views.WorkflowBuilderYamlView.as_view(), name="workflow_builder_yaml"),
    path("workflows/<slug:key>/builder/", views.WorkflowBuilderView.as_view(), name="workflow_builder_edit"),
    path("workflows/<slug:key>/launch/", views.WorkflowRunCreateView.as_view(), name="workflow_launch"),
    path("workflows/<slug:key>/inputs/<slug:child>/options/", views.InputOptionsView.as_view(), name="input_options"),
    path("runs/", views.WorkflowRunListView.as_view(), name="workflow_run_list"),
    path("runs/<uuid:pk>/", views.WorkflowRunDetailView.as_view(), name="workflow_run"),
    path("runs/<uuid:pk>/progress/", views.WorkflowRunProgressView.as_view(), name="workflow_run_progress"),
    path("runs/<uuid:pk>/cancel/", views.WorkflowRunCancelView.as_view(), name="workflow_run_cancel"),
    path("reports/", views.WorkflowReportView.as_view(), name="workflow_report"),
    path("docs/", RedirectView.as_view(url=static("nautobot_workflow_engine/docs/index.html")), name="docs"),
    path("", include(router.urls)),
]
