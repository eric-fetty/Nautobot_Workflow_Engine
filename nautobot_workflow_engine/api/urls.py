"""Builder API routes. Nautobot mounts this module at /api/plugins/workflow-engine/."""

from django.urls import path
from nautobot.apps.api import OrderedDefaultRouter

from .views import (
    BuilderContextView,
    BuilderDryRunView,
    BuilderFilterCountView,
    BuilderFilterFieldsView,
    BuilderLaunchPreviewView,
    BuilderRefactorView,
    BuilderRenderView,
    BuilderSchemaView,
    BuilderValidateView,
    BuilderYamlApplyView,
    WorkflowViewSet,
)
from .webhooks import GitWebhookView

router = OrderedDefaultRouter()
router.register("workflows", WorkflowViewSet, basename="workflow")

workflow_draft = WorkflowViewSet.as_view({"put": "draft", "delete": "draft"})
workflow_publish = WorkflowViewSet.as_view({"post": "publish_revision"})
workflow_enable = WorkflowViewSet.as_view({"post": "enable"})
workflow_disable = WorkflowViewSet.as_view({"post": "disable"})
workflow_revisions = WorkflowViewSet.as_view({"get": "revisions"})
workflow_revision = WorkflowViewSet.as_view({"get": "revision_detail"})
workflow_diff = WorkflowViewSet.as_view({"get": "revision_diff"})
workflow_restore = WorkflowViewSet.as_view({"post": "revision_restore"})
workflow_duplicate = WorkflowViewSet.as_view({"post": "duplicate"})
workflow_refresh = WorkflowViewSet.as_view({"post": "git_refresh"})
workflow_load_git = WorkflowViewSet.as_view({"post": "load_git"})
workflow_push = WorkflowViewSet.as_view({"post": "git_push"})
workflow_git_status = WorkflowViewSet.as_view({"get": "git_status"})
workflow_adopt = WorkflowViewSet.as_view({"post": "adopt"})
workflow_dry_run = WorkflowViewSet.as_view({"post": "dry_run"})
workflow_archive = WorkflowViewSet.as_view({"post": "archive"})
workflow_unarchive = WorkflowViewSet.as_view({"post": "unarchive"})

urlpatterns = [
    path("git/webhook/<uuid:repository_id>/", GitWebhookView.as_view(), name="git-webhook"),
    path("builder/schema/", BuilderSchemaView.as_view(), name="builder-schema"),
    path("builder/context/", BuilderContextView.as_view(), name="builder-context"),
    path("builder/validate/", BuilderValidateView.as_view(), name="builder-validate"),
    path("builder/refactor/", BuilderRefactorView.as_view(), name="builder-refactor"),
    path("builder/launch-preview/", BuilderLaunchPreviewView.as_view(), name="builder-launch-preview"),
    path("builder/dry-run/", BuilderDryRunView.as_view(), name="builder-dry-run"),
    path("builder/render/", BuilderRenderView.as_view(), name="builder-render"),
    path("builder/yaml/", BuilderYamlApplyView.as_view(), name="builder-yaml-apply"),
    path("builder/filter-count/", BuilderFilterCountView.as_view(), name="builder-filter-count"),
    path("builder/filter-fields/", BuilderFilterFieldsView.as_view(), name="builder-filter-fields"),
    path("workflows/<str:key>/dry-run/", workflow_dry_run, name="workflow-dry-run"),
    path("workflows/<str:key>/draft/", workflow_draft, name="workflow-draft"),
    path("workflows/<str:key>/publish/", workflow_publish, name="workflow-publish"),
    path("workflows/<str:key>/enable/", workflow_enable, name="workflow-enable"),
    path("workflows/<str:key>/disable/", workflow_disable, name="workflow-disable"),
    path("workflows/<str:key>/revisions/", workflow_revisions, name="workflow-revisions"),
    path("workflows/<str:key>/revisions/<int:number>/", workflow_revision, name="workflow-revision"),
    path("workflows/<str:key>/revisions/<int:number>/diff/", workflow_diff, name="workflow-revision-diff"),
    path("workflows/<str:key>/revisions/<int:number>/restore/", workflow_restore, name="workflow-revision-restore"),
    path("workflows/<str:key>/duplicate/", workflow_duplicate, name="workflow-duplicate"),
    path("workflows/<str:key>/git/refresh/", workflow_refresh, name="workflow-git-refresh"),
    path("workflows/<str:key>/git/load/", workflow_load_git, name="workflow-load-git"),
    path("workflows/<str:key>/git/push/", workflow_push, name="workflow-git-push"),
    path("workflows/<str:key>/git-status/", workflow_git_status, name="workflow-git-status"),
    path("workflows/<str:key>/adopt/", workflow_adopt, name="workflow-adopt"),
    path("workflows/<str:key>/archive/", workflow_archive, name="workflow-archive"),
    path("workflows/<str:key>/restore/", workflow_unarchive, name="workflow-restore"),
]
urlpatterns += router.urls
