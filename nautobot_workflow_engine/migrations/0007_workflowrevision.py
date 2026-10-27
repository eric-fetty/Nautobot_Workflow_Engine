# Generated manually for M2: revisions, archive flag, help text, and the when template column.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0006_workflow_managed_by_changelog"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkflowRevision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("number", models.PositiveIntegerField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("published", "Published"),
                            ("superseded", "Superseded"),
                            ("discarded", "Discarded"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("builder", "Builder"),
                            ("yaml", "YAML"),
                            ("api", "API"),
                            ("git", "Git"),
                            ("git_import", "Git import"),
                            ("duplicate", "Duplicate"),
                        ],
                        max_length=20,
                    ),
                ),
                ("definition", models.JSONField()),
                ("yaml", models.TextField(blank=True)),
                ("content_hash", models.CharField(blank=True, max_length=64)),
                ("comment", models.CharField(blank=True, max_length=200)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("git_commit", models.CharField(blank=True, max_length=40)),
                ("git_author", models.CharField(blank=True, max_length=200)),
                ("superseded_reason", models.CharField(blank=True, max_length=300)),
                (
                    "base_revision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="derived_revisions",
                        to="nautobot_workflow_engine.workflowrevision",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "published_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="nautobot_workflow_engine.workflow",
                    ),
                ),
            ],
            options={"ordering": ["number"]},
        ),
        migrations.AddConstraint(
            model_name="workflowrevision",
            constraint=models.UniqueConstraint(fields=("workflow", "number"), name="unique_revision_number"),
        ),
        migrations.AddConstraint(
            model_name="workflowrevision",
            constraint=models.UniqueConstraint(
                condition=models.Q(("state", "draft")), fields=("workflow",), name="one_draft_per_workflow"
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="archived",
            field=models.BooleanField(
                default=False, help_text="Hidden from launch and builder lists unless archived workflows are requested."
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="published_revision",
            field=models.ForeignKey(
                blank=True,
                help_text="The revision that raw_definition reflects. Drafts do not change this.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="nautobot_workflow_engine.workflowrevision",
            ),
        ),
        migrations.AddField(
            model_name="workflowinput",
            name="help_text",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name="workflowaction",
            name="when",
            field=models.TextField(blank=True),
        ),
    ]
