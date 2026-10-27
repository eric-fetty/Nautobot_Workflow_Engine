"""Fold "View workflows" and "Cancel runs" into "Run workflows".

Updates an untouched auto-created Run workflows entry, then deletes untouched, unassigned
View workflows and Cancel runs entries. See ``default_permissions.fold_view_and_cancel_into_run``.
"""

from django.db import migrations


def fold(apps, schema_editor):
    from nautobot_workflow_engine.default_permissions import fold_view_and_cancel_into_run

    fold_view_and_cancel_into_run(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0013_retire_default_permissions"),
    ]

    operations = [migrations.RunPython(fold, migrations.RunPython.noop)]
