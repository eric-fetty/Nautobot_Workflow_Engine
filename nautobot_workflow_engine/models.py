"""Database models for the workflow engine plugin."""

from django.conf import settings as dj_settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from nautobot.apps.models import BaseModel
from taggit.managers import TaggableManager


class Workflow(BaseModel):
    """A workflow definition with inputs and actions."""

    key = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    enabled = models.BooleanField(
        default=False,
        help_text="Whether this workflow is available for execution. Disabled workflows preserve run history.",
    )
    approval_required = models.BooleanField(
        default=False,
        help_text=(
            "When True on Nautobot 3.2+, git sync maintains a scoped ApprovalWorkflowDefinition for this "
            "workflow and immediate launches use a near-term ScheduledJob so they enter the approval pipeline."
        ),
    )
    tags = TaggableManager(blank=True)
    raw_definition = models.JSONField()
    source_repo = models.ForeignKey("extras.GitRepository", null=True, blank=True, on_delete=models.SET_NULL)
    manual_duration_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Duration in minutes it would take to perform this workflow manually without automation",
    )

    def get_absolute_url(self, **kwargs):
        """Return URL to launch this workflow."""
        from django.urls import reverse

        return reverse("plugins:nautobot_workflow_engine:workflow_launch", kwargs={"key": self.key})

    @property
    def is_available(self):
        """Check if workflow is available for execution."""
        return self.enabled

    @property
    def schedule_config(self):
        """Return workflow-level scheduling configuration from the raw definition."""
        schedule = (self.raw_definition or {}).get("schedule")
        return schedule if isinstance(schedule, dict) else {}

    @property
    def servicenow_schedule_config(self):
        """Return ServiceNow-specific scheduling configuration from the raw definition."""
        schedule = self.schedule_config
        servicenow = schedule.get("servicenow")
        config = servicenow.copy() if isinstance(servicenow, dict) else {}

        legacy_change_key = schedule.get("change_ticket_input_key")
        if legacy_change_key and "change_input_key" not in config:
            config["change_input_key"] = legacy_change_key

        return config

    @property
    def servicenow_change_input_key(self):
        """Return the workflow input key used as the ServiceNow change ticket source."""
        change_input_key = self.servicenow_schedule_config.get("change_input_key")
        return change_input_key.strip() if isinstance(change_input_key, str) and change_input_key.strip() else None

    def __str__(self):
        """Return the workflow name as string representation."""
        return self.name


class WorkflowInput(BaseModel):
    """Input field definition for a workflow."""

    TYPE_CHOICES = [
        ("string", "String"),
        ("integer", "Integer"),
        ("boolean", "Boolean"),
        ("choice", "Choice"),
        ("multichoice", "Multi-Choice"),
        ("object", "Object"),
        ("multiobject", "Multi-Object"),
        ("list", "List"),
        ("listdict", "List of Dictionaries"),
    ]

    workflow = models.ForeignKey(Workflow, related_name="inputs", on_delete=models.CASCADE)
    key = models.SlugField()
    label = models.CharField(max_length=200)
    input_type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    required = models.BooleanField(default=False)
    default = models.JSONField(null=True, blank=True)
    depends_on = models.JSONField(default=list, blank=True)
    filter_query = models.JSONField(null=True, blank=True)
    choices = models.JSONField(null=True, blank=True)
    columns = models.JSONField(null=True, blank=True)
    visible_when = models.JSONField(null=True, blank=True)
    validators = models.JSONField(
        null=True, blank=True
    )  # e.g. [{"regex": "...", "flags": ["IGNORECASE"], "message": "..."}]
    order = models.PositiveIntegerField(default=0)

    class Meta:
        """Meta class for WorkflowInput model."""

        unique_together = ("workflow", "key")
        ordering = ["order", "key"]

    def get_absolute_url(self, **kwargs):
        """Return URL to the parent workflow (no dedicated detail view)."""
        return self.workflow.get_absolute_url(**kwargs)

    def __str__(self):
        """Return string representation of the workflow input."""
        return f"{self.workflow}:{self.key}"


class WorkflowAction(BaseModel):
    """An action definition within a workflow."""

    ACTION_TYPES = [("nautobot", "Nautobot"), ("awx", "AWX")]
    PHASE_CHOICES = [
        ("pre", "Pre-Action"),
        ("main", "Main Action"),
        ("post", "Post-Action"),
    ]

    workflow = models.ForeignKey(Workflow, related_name="actions", on_delete=models.CASCADE)
    phase = models.CharField(max_length=20, choices=PHASE_CHOICES, default="main")
    order = models.PositiveIntegerField(default=0)
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    name = models.CharField(max_length=200)
    when = models.CharField(max_length=200, blank=True)

    # Nautobot - simplified to just reference the path
    script_path = models.CharField(max_length=255, blank=True)
    with_vars = models.JSONField(null=True, blank=True)

    # AWX
    awx_template_id = models.IntegerField(null=True, blank=True)
    awx_extra_vars = models.JSONField(null=True, blank=True)
    monitor = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        """Meta class for WorkflowAction model."""

        ordering = ["phase", "order", "id"]

    def get_absolute_url(self, **kwargs):
        """Return URL to the parent workflow since WorkflowAction doesn't have its own detail view."""
        return self.workflow.get_absolute_url(**kwargs)

    def __str__(self):
        """Return string representation of the workflow action."""
        return f"{self.workflow}:{self.name}"


class WorkflowRun(BaseModel):
    """A workflow execution instance."""

    SCHEDULE_SOURCE_CHOICES = [
        ("", "None"),
        ("manual", "Manual Schedule"),
        ("servicenow", "ServiceNow Change Window"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("pending_approval", "Pending Approval"),
        ("scheduled", "Scheduled"),
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("canceled", "Canceled"),
        ("partial", "Partial"),
    ]

    workflow = models.ForeignKey(Workflow, related_name="runs", on_delete=models.PROTECT)
    requested_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    inputs = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    started = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)
    log = models.TextField(blank=True)
    # User-facing output captured from print() calls in executed scripts
    user_log = models.TextField(blank=True)
    phase_results = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-phase execution results and metadata for pre/main/post action orchestration",
    )

    # Actual time the user spent filling out the launch form (in seconds)
    form_duration_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Measured duration, in seconds, that the user spent filling out the workflow launch form",
    )

    # Scheduling fields
    scheduled_for = models.DateTimeField(null=True, blank=True, help_text="When this workflow is scheduled to execute")
    scheduled_job = models.ForeignKey(
        "extras.ScheduledJob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Associated Nautobot scheduled job for delayed execution",
    )
    schedule_source = models.CharField(max_length=32, choices=SCHEDULE_SOURCE_CHOICES, default="", blank=True)
    schedule_external_system = models.CharField(max_length=64, blank=True)
    schedule_change_ticket = models.CharField(max_length=128, blank=True)
    schedule_external_record_id = models.CharField(max_length=128, blank=True)
    schedule_window_start = models.DateTimeField(null=True, blank=True)
    schedule_window_end = models.DateTimeField(null=True, blank=True)
    schedule_last_synced = models.DateTimeField(null=True, blank=True)
    schedule_sync_error = models.TextField(blank=True)

    def get_absolute_url(self, **kwargs):
        """Return URL to this workflow run detail."""
        from django.urls import reverse

        return reverse("plugins:nautobot_workflow_engine:workflow_run", kwargs={"pk": self.pk})

    def __str__(self):
        """Return string representation of the workflow run."""
        return f"Run {self.pk} - {self.workflow.name}"


class AwxRun(BaseModel):
    """An AWX job execution record associated with a workflow run."""

    workflow_run = models.ForeignKey(WorkflowRun, related_name="awx_runs", on_delete=models.CASCADE)
    template_id = models.IntegerField()
    job_id = models.IntegerField(null=True, blank=True)
    url = models.URLField(blank=True)
    status = models.CharField(max_length=30, default="pending")
    raw = models.JSONField(null=True, blank=True)

    def get_absolute_url(self, **kwargs):
        """Return URL to the parent workflow run (no dedicated detail view)."""
        return self.workflow_run.get_absolute_url(**kwargs)

    def __str__(self):
        """Return string representation of the AWX run."""
        return f"AWX job {self.job_id or '?'} for run {self.workflow_run_id}"


class WorkflowHelper(BaseModel):
    """Store helper Python files from Git repositories for modular script support."""

    source_repo = models.ForeignKey("extras.GitRepository", on_delete=models.CASCADE)
    file_path = models.CharField(max_length=500)  # e.g., "scripts/utils.py"
    content = models.TextField()

    class Meta:
        """Meta class for WorkflowHelper model."""

        unique_together = ("source_repo", "file_path")

    def get_absolute_url(self, **kwargs):
        """Return URL to the source Git repository (no dedicated detail view).

        Required so Nautobot change-logging can serialize create/delete events during git sync.
        """
        return self.source_repo.get_absolute_url(**kwargs)

    def __str__(self):
        """Return string representation of the WorkflowHelper."""
        return f"{self.source_repo.name}:{self.file_path}"


class WorkflowFilterModule(BaseModel):
    """Store Jinja filter Python files synced from Git repositories."""

    source_repo = models.ForeignKey("extras.GitRepository", on_delete=models.CASCADE)
    file_path = models.CharField(max_length=500)  # e.g., "filters/networking.py"
    content = models.TextField()

    class Meta:
        """Meta class for WorkflowFilterModule model."""

        unique_together = ("source_repo", "file_path")

    def get_absolute_url(self, **kwargs):
        """Return URL to the source Git repository (no dedicated detail view).

        Required so Nautobot change-logging can serialize create/delete events during git sync.
        """
        return self.source_repo.get_absolute_url(**kwargs)

    def __str__(self):
        """Return string representation of the WorkflowFilterModule."""
        return f"{self.source_repo.name}:{self.file_path}"
