"""Filters for nautobot_workflow_engine."""

import uuid

import django_filters
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.filters import BaseFilterSet, NautobotFilterSet, SearchFilter

from .models import Workflow, WorkflowRun, WorkflowRunObjectReference


class WorkflowFilterSet(NautobotFilterSet):
    """FilterSet for Workflow model."""

    q = SearchFilter(
        filter_predicates={
            "name": "icontains",
            "description": "icontains",
            "category": "icontains",
        },
    )

    class Meta:
        """Meta class for WorkflowFilterSet."""

        model = Workflow
        fields = ["git_status", "managed_by", "enabled", "archived", "category"]
        exclude = ["tags"]


def content_type_for_label(label):
    """Return the ContentType for an ``app_label.model`` string, or None."""
    if not isinstance(label, str) or label.count(".") != 1:
        return None
    app_label, model = label.strip().lower().split(".")
    try:
        return ContentType.objects.get_by_natural_key(app_label, model)
    except ContentType.DoesNotExist:
        return None


def runs_referencing(queryset, object_id, content_type=None):
    """Narrow ``queryset`` to runs whose form picked the object ``object_id`` (optionally of one type)."""
    refs = WorkflowRunObjectReference.objects.filter(object_id=object_id)
    if content_type is not None:
        refs = refs.filter(content_type=content_type)
    return queryset.filter(pk__in=refs.values("workflow_run_id"))


class WorkflowRunFilterSet(BaseFilterSet):
    """FilterSet for workflow runs: search plus "runs that used this object"."""

    q = SearchFilter(
        filter_predicates={
            "workflow__name": "icontains",
            "workflow__key": "icontains",
            "workflow__category": "icontains",
            "requested_by__username": "icontains",
            "status": "icontains",
            "schedule_change_ticket": "icontains",
            "search_text": "icontains",
        },
    )
    workflow = django_filters.CharFilter(field_name="workflow__key", label="Workflow (key)")
    status = django_filters.MultipleChoiceFilter(choices=WorkflowRun.STATUS_CHOICES)
    user = django_filters.CharFilter(field_name="requested_by__username", label="Started by (username)")
    referenced_object = django_filters.CharFilter(method="filter_referenced_object", label="Used object (ID)")
    referenced_object_type = django_filters.CharFilter(
        method="filter_referenced_object_type", label="Used object type (app_label.model)"
    )

    class Meta:
        """Meta class for WorkflowRunFilterSet."""

        model = WorkflowRun
        fields = ["id"]

    def filter_referenced_object(self, queryset, name, value):  # pylint: disable=unused-argument
        """Runs whose form picked this object, optionally limited by ``referenced_object_type``."""
        try:
            object_id = uuid.UUID(str(value).strip())
        except (TypeError, ValueError):
            return queryset.none()
        content_type = None
        type_label = self.data.get("referenced_object_type") if hasattr(self.data, "get") else None
        if type_label:
            content_type = content_type_for_label(type_label)
            if content_type is None:
                return queryset.none()
        return runs_referencing(queryset, object_id, content_type)

    def filter_referenced_object_type(self, queryset, name, value):  # pylint: disable=unused-argument
        """Runs whose form picked any object of this type, unless a single object is also given."""
        if hasattr(self.data, "get") and self.data.get("referenced_object"):
            return queryset  # applied together with referenced_object
        content_type = content_type_for_label(value)
        if content_type is None:
            return queryset.none()
        refs = WorkflowRunObjectReference.objects.filter(content_type=content_type)
        return queryset.filter(pk__in=refs.values("workflow_run_id"))


__all__ = ("WorkflowFilterSet", "WorkflowRunFilterSet", "content_type_for_label", "runs_referencing")
