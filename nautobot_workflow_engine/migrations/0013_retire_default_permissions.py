"""Remove the auto-created "Author templates and scripts" and "Approve runs" permissions.

Only untouched, unassigned copies are deleted; see ``default_permissions.retire_old_permissions``.
"""

from django.db import migrations


def retire(apps, schema_editor):
    from nautobot_workflow_engine.default_permissions import retire_old_permissions

    retire_old_permissions(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0012_fold_publish_provider"),
        ("users", "0010_user_default_saved_views"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(retire, migrations.RunPython.noop)]
