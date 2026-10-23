# Generated manually for M1: changelog timestamps, ownership, and approval snapshots.

from django.db import migrations, models


def set_managed_by(apps, schema_editor):
    """Git-sourced rows stay Git-managed. Rows with no repository are UI-owned."""
    workflow = apps.get_model("nautobot_workflow_engine", "Workflow")
    workflow.objects.filter(source_repo__isnull=False).update(managed_by="git")
    workflow.objects.filter(source_repo__isnull=True).update(managed_by="nautobot")


def noop_reverse(apps, schema_editor):
    """Ownership is derived again if the migration is reapplied."""


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0005_workflowrun_workflow_related_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="created",
            field=models.DateTimeField(auto_now_add=True, blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflow",
            name="last_updated",
            field=models.DateTimeField(auto_now=True, blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflow",
            name="managed_by",
            field=models.CharField(
                choices=[("nautobot", "Nautobot"), ("git", "Git")],
                default="nautobot",
                help_text=(
                    "nautobot: edited in the UI and left alone by Git sync. "
                    "git: the repository file is the source of truth."
                ),
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="approval_stage_snapshot",
            field=models.JSONField(
                blank=True,
                help_text="Approval stages saved when approval is turned off, so they can be restored.",
                null=True,
            ),
        ),
        migrations.RunPython(set_managed_by, noop_reverse),
    ]
