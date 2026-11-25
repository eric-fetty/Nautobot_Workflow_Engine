# Runs remember the freeze that blocked them, or the override used to start them.
# The new "blocked" status is a choices change only, so no data is touched.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0018_changefreeze"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="freeze",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="runs",
                to="nautobot_workflow_engine.changefreeze",
            ),
        ),
        migrations.AddField(
            model_name="workflowrun",
            name="freeze_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="workflowrun",
            name="freeze_override_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="workflowrun",
            name="freeze_override_reason",
            field=models.TextField(blank=True),
        ),
    ]
