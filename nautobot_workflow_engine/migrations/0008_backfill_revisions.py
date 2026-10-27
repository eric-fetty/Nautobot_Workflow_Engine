# Backfill one published revision per workflow without rewriting raw_definition.

from django.db import migrations


def backfill_revisions(apps, schema_editor):
    """Create r1 from the definition that is already stored.

    ``source`` is ``git_import`` when Git owns the row and ``builder`` otherwise.
    ``raw_definition`` is copied, not normalized, so the migration does not change it.
    """
    from nautobot_workflow_engine.schema.yaml_io import content_hash, to_yaml

    workflow_model = apps.get_model("nautobot_workflow_engine", "Workflow")
    revision_model = apps.get_model("nautobot_workflow_engine", "WorkflowRevision")
    for workflow in workflow_model.objects.all().iterator():
        if workflow.published_revision_id:
            continue
        definition = workflow.raw_definition if isinstance(workflow.raw_definition, dict) else {}
        text = to_yaml(definition)
        source = "git_import" if workflow.managed_by == "git" else "builder"
        revision = revision_model.objects.create(
            workflow=workflow,
            number=1,
            state="published",
            source=source,
            definition=definition,
            yaml=text,
            content_hash=content_hash(text),
            published_at=workflow.last_updated,
        )
        workflow.published_revision = revision
        workflow.save(update_fields=["published_revision"])


def noop_reverse(apps, schema_editor):
    """Leave revisions in place if this migration is reversed."""


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0007_workflowrevision"),
    ]

    operations = [
        migrations.RunPython(backfill_revisions, noop_reverse),
    ]
