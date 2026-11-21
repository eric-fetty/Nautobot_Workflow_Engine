# Switch Workflow.tags from plain django-taggit to Nautobot tags (extras.Tag).

import logging

import nautobot.core.models.fields
from django.db import migrations, models

logger = logging.getLogger(__name__)


def report_legacy_taggit_rows(apps, schema_editor):
    """Log any django-taggit rows recorded against workflows, and leave them in place.

    Workflows have UUID primary keys, but taggit's own ``TaggedItem.object_id`` is an
    integer column, so it cannot point at a workflow and no usable rows can exist. The
    builder only ever offered Nautobot tags. This step only reports what it finds, so
    nothing is deleted and rolling back loses nothing.
    """
    try:
        content_type_model = apps.get_model("contenttypes", "ContentType")
        taggit_item_model = apps.get_model("taggit", "TaggedItem")
    except LookupError:
        return
    content_type = content_type_model.objects.filter(app_label="nautobot_workflow_engine", model="workflow").first()
    if content_type is None:
        return
    count = taggit_item_model.objects.filter(content_type_id=content_type.pk).count()
    if count:
        logger.warning(
            "Found %s django-taggit row(s) on workflows. They cannot refer to a workflow (integer object IDs), "
            "so they were left unchanged. Assign Nautobot tags to workflows instead.",
            count,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        # Same pin as 0001: the last extras migration present in Nautobot 3.0.0 (min supported).
        ("extras", "0132_approval_workflow_seed_data"),
        ("taggit", "0006_rename_taggeditem_content_type_object_id_taggit_tagg_content_8fc721_idx"),
        ("nautobot_workflow_engine", "0016_backfill_run_references"),
    ]

    operations = [
        migrations.RunPython(report_legacy_taggit_rows, migrations.RunPython.noop),
        # Django can't change an M2M's "through" in place, so drop the taggit field and add the Nautobot one.
        # Neither step touches tag rows: taggit and extras both keep theirs in generic tables.
        migrations.RemoveField(model_name="workflow", name="tags"),
        migrations.AddField(
            model_name="workflow",
            name="tags",
            field=nautobot.core.models.fields.TagsField(through="extras.TaggedItem", to="extras.Tag"),
        ),
        # Unpublished drafts keep raw_definition empty. blank=True lets Nautobot's Bulk Edit job
        # (which runs full_clean) tag them. Schema-only: no SQL change.
        migrations.AlterField(
            model_name="workflow",
            name="raw_definition",
            field=models.JSONField(blank=True),
        ),
    ]
