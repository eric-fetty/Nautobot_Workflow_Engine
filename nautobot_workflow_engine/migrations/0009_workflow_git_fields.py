# Git status columns for two-way sync.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        # Same pin as 0001: last extras migration in Nautobot 3.0.0.
        ("extras", "0132_approval_workflow_seed_data"),
        ("nautobot_workflow_engine", "0008_backfill_revisions"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="git_path",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Path of this workflow's YAML file relative to the repository root.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="git_status",
            field=models.CharField(
                choices=[
                    ("not_configured", "Not in Git"),
                    ("pending", "Syncing to Git"),
                    ("in_sync", "In Git"),
                    ("not_synced", "Not synced to Git"),
                    ("git_invalid", "Git version invalid"),
                    ("disabled_repo", "Publishing off"),
                ],
                default="not_configured",
                help_text="Whether the database copy matches the file in Git.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="git_commit",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Commit at the last moment the database and Git were equal.",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="git_content_hash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="SHA-256 of the file bytes at that same moment.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="git_error",
            field=models.TextField(blank=True, default="", help_text="Sanitized reason the last Git operation failed."),
        ),
        migrations.AddField(
            model_name="workflow",
            name="git_job_result",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="+",
                to="extras.jobresult",
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="git_retry_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="workflow",
            name="git_next_retry_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
