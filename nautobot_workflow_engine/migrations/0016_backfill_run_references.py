# Index runs recorded before run history search existed.

from django.db import migrations


def backfill_run_index(apps, schema_editor):
    """Fill ``search_text`` and object references for every existing run.

    Uses each workflow's current input definitions (the only ones stored) and looks
    picked objects up through the live model registry so their names are recorded.
    Objects that are gone, or that can't be read, keep a reference with no name.
    """
    from django.apps import apps as live_apps

    from nautobot_workflow_engine.run_index import build_run_index, display_resolver, input_definitions

    content_type_model = apps.get_model("contenttypes", "ContentType")
    workflow_input_model = apps.get_model("nautobot_workflow_engine", "WorkflowInput")
    run_model = apps.get_model("nautobot_workflow_engine", "WorkflowRun")
    reference_model = apps.get_model("nautobot_workflow_engine", "WorkflowRunObjectReference")

    def model_for(content_type_id):
        content_type = content_type_model.objects.filter(pk=content_type_id).first()
        if content_type is None:
            return None
        try:
            return live_apps.get_model(content_type.app_label, content_type.model)
        except LookupError:
            return None

    resolver = display_resolver(model_for)
    defs_by_workflow = {}
    for run in run_model.objects.all().only("id", "workflow_id", "inputs").iterator(chunk_size=500):
        if run.workflow_id not in defs_by_workflow:
            defs_by_workflow[run.workflow_id] = input_definitions(
                workflow_input_model.objects.filter(workflow_id=run.workflow_id)
            )

        def safe_resolver(content_type_id, ids):
            try:
                return resolver(content_type_id, ids)
            except Exception:  # noqa: BLE001 - a broken lookup must not stop the upgrade
                return {}

        references, text = build_run_index(run.inputs, defs_by_workflow[run.workflow_id], safe_resolver)
        reference_model.objects.filter(workflow_run_id=run.pk).delete()
        reference_model.objects.bulk_create(
            [reference_model(workflow_run_id=run.pk, **ref) for ref in references],
            ignore_conflicts=True,
        )
        run_model.objects.filter(pk=run.pk).update(search_text=text)


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0015_run_object_references"),
    ]

    operations = [
        migrations.RunPython(backfill_run_index, migrations.RunPython.noop),
    ]
