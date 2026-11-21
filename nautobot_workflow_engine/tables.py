"""Tables for nautobot_workflow_engine."""

import django_tables2 as tables
from django.utils.html import format_html
from nautobot.apps.tables import BaseTable

from .models import Workflow, WorkflowRun

# Matches the status badges on the Run history page.
RUN_STATUS_BADGES = {
    "success": ("bg-success", "mdi-check"),
    "failed": ("bg-danger", "mdi-close"),
    "running": ("bg-primary", "mdi-play"),
    "pending": ("bg-warning text-dark", "mdi-clock"),
    "pending_approval": ("nwe-status-pending-approval bg-warning text-dark", "mdi-account-check"),
    "scheduled": ("bg-info text-dark", "mdi-calendar-clock"),
    "canceled": ("bg-secondary", "mdi-cancel"),
    "partial": ("bg-warning text-dark", "mdi-alert"),
}


def run_status_badge(run):
    """Return the coloured status badge HTML for a run."""
    css, icon = RUN_STATUS_BADGES.get(run.status, ("bg-secondary", "mdi-help"))
    return format_html('<span class="badge {}"><i class="mdi {}"></i> {}</span>', css, icon, run.get_status_display())


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


class WorkflowRunTable(BaseTable):
    """Workflow runs, as shown in Nautobot's global search results."""

    started = tables.DateTimeColumn(linkify=True, verbose_name="Started", format="M d, Y H:i")
    workflow = tables.Column(accessor="workflow__name", verbose_name="Workflow")
    status = tables.Column(verbose_name="Status")
    requested_by = tables.Column(accessor="requested_by__username", verbose_name="Started by")
    schedule_change_ticket = tables.Column(verbose_name="Change ticket")

    class Meta(BaseTable.Meta):
        """Meta class for WorkflowRunTable."""

        model = WorkflowRun
        fields = ("started", "workflow", "status", "requested_by", "schedule_change_ticket")
        default_columns = fields

    def render_status(self, record):
        """Show the status as the same badge the Run history page uses."""
        return run_status_badge(record)
