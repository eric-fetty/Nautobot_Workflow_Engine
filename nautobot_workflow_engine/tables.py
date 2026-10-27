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
    git_status = tables.Column(verbose_name="Git")

    class Meta(BaseTable.Meta):
        """Meta class for WorkflowTable."""

        model = Workflow
        fields = ("pk", "name", "category", "description", "git_status")
        default_columns = ("name", "category", "description", "git_status")
