# Change freezes: windows when some or all workflows can't start.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        # Same pin as 0001: the last extras migration present in Nautobot 3.0.0 (min supported).
        ("extras", "0132_approval_workflow_seed_data"),
        ("nautobot_workflow_engine", "0017_workflow_nautobot_tags"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ChangeFreeze",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("message", models.TextField(blank=True, max_length=500)),
                ("enabled", models.BooleanField(default=True)),
                ("timezone", models.CharField(default="UTC", max_length=64)),
                ("kind", models.CharField(default="one_time", max_length=16)),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("recur_start_date", models.DateField(blank=True, null=True)),
                ("recur_start_time", models.TimeField(blank=True, null=True)),
                ("rrule", models.CharField(blank=True, max_length=500)),
                ("duration_minutes", models.PositiveIntegerField(blank=True, null=True)),
                ("recur_until", models.DateField(blank=True, null=True)),
                ("warning_lead_minutes", models.PositiveIntegerField(default=120)),
                ("applies_to_all", models.BooleanField(default=False)),
                ("categories", models.JSONField(blank=True, default=list)),
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
                    "excluded_workflows",
                    models.ManyToManyField(
                        blank=True, related_name="change_freeze_exclusions", to="nautobot_workflow_engine.workflow"
                    ),
                ),
                ("match_tags", models.ManyToManyField(blank=True, related_name="+", to="extras.tag")),
                (
                    "workflows",
                    models.ManyToManyField(
                        blank=True, related_name="change_freezes", to="nautobot_workflow_engine.workflow"
                    ),
                ),
            ],
            options={
                "ordering": ("name",),
            },
        ),
    ]
