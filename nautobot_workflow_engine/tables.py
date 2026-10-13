"""Tables for nautobot_workflow_engine."""

import django_tables2 as tables
from nautobot.apps.tables import BaseTable

from .models import Workflow


class WorkflowTable(BaseTable):
    """Table for displaying Workflow objects."""

    pk = tables.CheckBoxColumn()
    name = tables.LinkColumn()
    category = tables.Column()
    description = tables.Column()

    class Meta(BaseTable.Meta):
        """Meta class for WorkflowTable."""

        model = Workflow
        fields = ("pk", "name", "category", "description")
        default_columns = ("name", "category", "description")
