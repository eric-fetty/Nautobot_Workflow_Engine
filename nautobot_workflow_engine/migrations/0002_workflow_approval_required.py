# Generated manually for approval_required support

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="approval_required",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When True on Nautobot 3.2+, git sync maintains a scoped ApprovalWorkflowDefinition for this "
                    "workflow and immediate launches use a near-term ScheduledJob so they enter the approval pipeline."
                ),
            ),
        ),
    ]
