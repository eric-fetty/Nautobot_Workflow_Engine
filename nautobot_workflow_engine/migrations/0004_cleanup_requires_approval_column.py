# Clean up leftover requires_approval column from an earlier experimental migration.
#
# Dev instances that applied 0002_workflow_requires_approval before the field was
# renamed to approval_required can end up with an orphaned NOT NULL column that the
# ORM no longer populates, causing IntegrityError on Workflow create during git sync.

from django.db import migrations

STALE_MIGRATION_NAMES = (
    "0002_workflow_requires_approval",
    "0003_alter_workflow_requires_approval",
    "0003_workflow_approval_required_default",
)


def _column_names(schema_editor, table):
    """Return lowercased column names for the given table."""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table)
    return {column.name.lower() for column in description}


def cleanup_requires_approval_column(apps, schema_editor):
    """Rename or drop orphaned requires_approval so only approval_required remains."""
    table = apps.get_model("nautobot_workflow_engine", "Workflow")._meta.db_table
    quote = schema_editor.connection.ops.quote_name
    columns = _column_names(schema_editor, table)
    has_requires = "requires_approval" in columns
    has_approval = "approval_required" in columns

    if not has_requires:
        return

    if has_approval:
        # Prefer existing approval_required values; fill nulls from the legacy column.
        schema_editor.execute(
            f"UPDATE {quote(table)} SET {quote('approval_required')} = "
            f"COALESCE({quote('approval_required')}, {quote('requires_approval')}, FALSE)"
        )
        schema_editor.execute(f"ALTER TABLE {quote(table)} DROP COLUMN {quote('requires_approval')}")
    else:
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} RENAME COLUMN {quote('requires_approval')} TO {quote('approval_required')}"
        )


def cleanup_stale_migration_records(apps, schema_editor):
    """Remove django_migrations rows for superseded experimental migration modules."""
    connection = schema_editor.connection
    placeholders = ", ".join(["%s"] * len(STALE_MIGRATION_NAMES))
    with connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM django_migrations WHERE app = %s AND name IN ({placeholders})",
            ["nautobot_workflow_engine", *STALE_MIGRATION_NAMES],
        )


def noop_reverse(apps, schema_editor):
    """Irreversible data/schema cleanup."""


class Migration(migrations.Migration):
    dependencies = [
        ("nautobot_workflow_engine", "0003_noop"),
    ]

    operations = [
        migrations.RunPython(cleanup_requires_approval_column, noop_reverse),
        migrations.RunPython(cleanup_stale_migration_records, noop_reverse),
    ]
