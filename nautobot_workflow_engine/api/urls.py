"""Builder API routes. Nautobot mounts this module at /api/plugins/workflow-engine/."""

from django.urls import path
from nautobot.apps.api import OrderedDefaultRouter

from .views import (
    BuilderContextView,
    BuilderRefactorView,
    BuilderSchemaView,
    BuilderValidateView,
    WorkflowViewSet,
)

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

urlpatterns = [
    path("builder/schema/", BuilderSchemaView.as_view(), name="builder-schema"),
    path("builder/context/", BuilderContextView.as_view(), name="builder-context"),
    path("builder/validate/", BuilderValidateView.as_view(), name="builder-validate"),
    path("builder/refactor/", BuilderRefactorView.as_view(), name="builder-refactor"),
    path("workflows/<str:key>/draft/", workflow_draft, name="workflow-draft"),
    path("workflows/<str:key>/publish/", workflow_publish, name="workflow-publish"),
    path("workflows/<str:key>/enable/", workflow_enable, name="workflow-enable"),
    path("workflows/<str:key>/disable/", workflow_disable, name="workflow-disable"),
    path("workflows/<str:key>/revisions/", workflow_revisions, name="workflow-revisions"),
    path("workflows/<str:key>/revisions/<int:number>/", workflow_revision, name="workflow-revision"),
    path("workflows/<str:key>/revisions/<int:number>/diff/", workflow_diff, name="workflow-revision-diff"),
    path("workflows/<str:key>/revisions/<int:number>/restore/", workflow_restore, name="workflow-revision-restore"),
    path("workflows/<str:key>/duplicate/", workflow_duplicate, name="workflow-duplicate"),
]
urlpatterns += router.urls
