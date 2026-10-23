# Launch-form placeholder text for a workflow input.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0009_workflow_git_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowinput",
            name="placeholder",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
