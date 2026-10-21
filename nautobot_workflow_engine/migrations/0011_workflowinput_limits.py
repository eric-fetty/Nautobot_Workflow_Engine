# Optional range, list, and table limits, plus the CSV import switch.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0010_workflowinput_placeholder"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowinput",
            name="min_value",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowinput",
            name="max_value",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowinput",
            name="min_items",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowinput",
            name="max_items",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowinput",
            name="min_rows",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowinput",
            name="max_rows",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowinput",
            name="csv_import",
            field=models.BooleanField(default=True),
        ),
    ]
