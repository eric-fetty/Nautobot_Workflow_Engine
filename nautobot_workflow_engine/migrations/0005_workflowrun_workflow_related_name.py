# Generated manually: related_name is ORM-only (no database schema change).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Expose Workflow.runs reverse relation for WorkflowRun."""

    dependencies = [
        ("nautobot_workflow_engine", "0004_cleanup_requires_approval_column"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workflowrun",
            name="workflow",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="runs",
                to="nautobot_workflow_engine.workflow",
            ),
        ),
    ]
