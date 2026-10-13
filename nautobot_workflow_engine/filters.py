"""Filters for nautobot_workflow_engine."""

from nautobot.apps.filters import NautobotFilterSet, SearchFilter

from .models import Workflow


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
        exclude = ["tags"]
