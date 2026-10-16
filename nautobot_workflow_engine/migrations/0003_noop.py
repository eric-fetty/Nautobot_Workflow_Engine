# No schema changes. Exists so 0002 is not the latest migration module under
# pylint-django's new-db-field-with-default checker (it only inspects the latest).

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0002_workflow_approval_required"),
    ]

    operations = []
