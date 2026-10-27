"""Publishing now uses the "workflows" provided content. Drop the old second provider.

Older builds registered ``nautobot_workflow_engine.workflow_publish`` as a separate
Git repository content type. It is no longer registered, so a stored value would make
the repository's edit form reject the save. This removes it from every repository and
leaves everything else as it was. A repository that had only the old value keeps no
workflow content; ticking "workflows" makes it a publish target again.
"""

from django.db import migrations

LEGACY_PUBLISH_CONTENT = "nautobot_workflow_engine.workflow_publish"


def drop_legacy_provider(apps, schema_editor):
    """Remove the legacy publish content type from ``provided_contents``."""
    GitRepository = apps.get_model("extras", "GitRepository")
    for repository in GitRepository.objects.all().only("pk", "provided_contents"):
        contents = list(repository.provided_contents or [])
        if LEGACY_PUBLISH_CONTENT not in contents:
            continue
        repository.provided_contents = [item for item in contents if item != LEGACY_PUBLISH_CONTENT]
        repository.save(update_fields=["provided_contents"])


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0132_approval_workflow_seed_data"),
        ("nautobot_workflow_engine", "0011_workflowinput_limits"),
    ]

    operations = [
        migrations.RunPython(drop_legacy_provider, migrations.RunPython.noop),
    ]
