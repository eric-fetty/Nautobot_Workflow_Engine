"""Tables for nautobot_workflow_engine."""

import django_tables2 as tables
from django.utils.html import format_html
from nautobot.apps.tables import BaseTable, TagColumn

from . import freezes as freeze_rules
from .models import ChangeFreeze, Workflow, WorkflowRun

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
    "blocked": ("text-bg-dark", "mdi-lock-outline"),
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
    tags = TagColumn(url_name="plugins:nautobot_workflow_engine:workflows_list")

    class Meta(BaseTable.Meta):
        """Meta class for WorkflowTable."""

        model = Workflow
        fields = ("pk", "name", "category", "description", "git_status", "tags")
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


FREEZE_STATE_PILLS = {
    freeze_rules.STATE_ACTIVE: "bg-danger",
    freeze_rules.STATE_UPCOMING: "bg-warning text-dark",
    freeze_rules.STATE_EXPIRED: "bg-secondary",
    freeze_rules.STATE_DISABLED: "bg-white text-secondary border border-secondary",
}


def freeze_state_pill(freeze, now=None):
    """The state pill (Active now, Upcoming, Expired, Disabled)."""
    state = freeze_rules.freeze_state(freeze, now)
    icon = "mdi-lock-outline" if state == freeze_rules.STATE_ACTIVE else "mdi-calendar-clock"
    return format_html(
        '<span class="badge {}" data-freeze-state="{}"><i class="mdi {}"></i> {}</span>',
        FREEZE_STATE_PILLS[state],
        state,
        icon,
        freeze_rules.STATE_LABELS[state],
    )


def freeze_scope_summary(freeze):
    """``All workflows`` or ``Categories: A · Tags: B · 2 workflows`` (minus N excluded)."""
    if freeze.applies_to_all:
        parts = ["All workflows"]
    else:
        parts = []
        if freeze.categories:
            parts.append("Categories: " + ", ".join(freeze.categories))
        tags = [tag.name for tag in freeze.match_tags.all()]
        if tags:
            parts.append("Tags: " + ", ".join(tags))
        listed = len(freeze.workflows.all())
        if listed:
            parts.append(f"{listed} workflow{'s' if listed != 1 else ''}")
    excluded = len(freeze.excluded_workflows.all())
    if excluded:
        parts.append(f"minus {excluded} excluded")
    return " \u00b7 ".join(parts) or "Nothing"


class ChangeFreezeTable(BaseTable):
    """Change freezes with their state, schedule, scope and reach."""

    pk = tables.CheckBoxColumn()
    name = tables.LinkColumn()
    state = tables.Column(empty_values=(), orderable=False, verbose_name="State")
    schedule = tables.Column(empty_values=(), orderable=False, verbose_name="Schedule")
    next_start = tables.Column(empty_values=(), orderable=False, verbose_name="Next start")
    scope = tables.Column(empty_values=(), orderable=False, verbose_name="Scope")
    affects = tables.Column(empty_values=(), orderable=False, verbose_name="Affects")
    warning_lead_minutes = tables.Column(verbose_name="Warning (min)")
    enabled = tables.BooleanColumn()

    class Meta(BaseTable.Meta):
        """Meta class for ChangeFreezeTable."""

        model = ChangeFreeze
        fields = (
            "pk",
            "name",
            "state",
            "schedule",
            "next_start",
            "scope",
            "affects",
            "warning_lead_minutes",
            "enabled",
        )
        default_columns = ("pk", "name", "state", "schedule", "next_start", "scope", "affects")

    def render_state(self, record):
        """State pill."""
        return freeze_state_pill(record)

    def render_schedule(self, record):
        """Plain-language schedule."""
        return freeze_rules.describe_schedule(record)

    def render_next_start(self, record):
        """Start of the current or next window, in the freeze's zone."""
        windows = freeze_rules.next_windows(record, count=1) if record.enabled else []
        if not windows:
            return format_html('<span class="text-secondary">\u2014</span>')
        return freeze_rules.format_instant(windows[0].start, record)

    def render_scope(self, record):
        """Scope summary."""
        return freeze_scope_summary(record)

    def render_affects(self, record):
        """How many workflows the freeze covers."""
        count = len(freeze_rules.affected_workflows(record))
        return f"{count} workflow{'s' if count != 1 else ''}"
