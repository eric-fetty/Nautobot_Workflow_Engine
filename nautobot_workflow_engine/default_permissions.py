"""Object permissions the app creates on install, ready for an admin to assign.

They are created on every ``nautobot-server migrate`` / ``post_upgrade`` (via
``nautobot_database_ready``) only when no permission with the same name exists, so an
admin's edits, renames of actions or constraints, and assignments are never touched.
No users or groups are ever assigned here.
"""

import logging

logger = logging.getLogger(__name__)

APP = "nautobot_workflow_engine"

# (name, description, [(app_label, model)], [actions])
DEFAULT_PERMISSIONS = (
    (
        "Workflow Engine: View workflows",
        "See published workflows on the Workflows list and open their launch page. Add a constraint, "
        'for example {"category": "Networking"}, to limit which workflows.',
        [(APP, "workflow")],
        ["view"],
    ),
    (
        "Workflow Engine: Run workflows",
        "Start runs. Pair it with “View workflows” to control which workflows a person can run.",
        [(APP, "workflowrun")],
        ["add"],
    ),
    (
        "Workflow Engine: View runs",
        "See runs in Run History, their details, and the dashboard figures built from them.",
        [(APP, "workflowrun")],
        ["view"],
    ),
    (
        "Workflow Engine: Cancel runs",
        "Cancel a run that is still Scheduled or Pending Approval.",
        [(APP, "workflowrun")],
        ["change"],
    ),
    (
        "Workflow Engine: Build workflows",
        "Create workflows and edit the shared draft in the builder: inputs, steps, templates, conditions, "
        "script choices, the YAML view and Preview run. Publishing needs “Publish workflows” as well.",
        [(APP, "workflow")],
        ["view", "add", "change"],
    ),
    (
        "Workflow Engine: Publish workflows",
        "Publish changes, switch a workflow on or off, and retry a Git push. Without it the builder offers Request publish.",
        [(APP, "workflow")],
        ["publish"],
    ),
    (
        "Workflow Engine: Write scripts",
        "Create a new script from the builder and commit it to the repository. The builder never runs it.",
        [(APP, "workflow")],
        ["write_script"],
    ),
)


# Entries earlier builds created and this one no longer does, exactly as they were created.
# "Author templates and scripts" is now part of "Build workflows". "Approve runs" is gone:
# approvers come from Nautobot's own approval workflow definitions.
RETIRED_PERMISSIONS = (
    (
        "Workflow Engine: Author templates and scripts",
        "Write templates beyond a single inputs or results reference, change a step's script, and use the YAML view.",
        [(APP, "workflow")],
        ["author_code"],
    ),
    (
        "Workflow Engine: Approve runs",
        "Open and approve or deny approval stages for workflow runs that need approval. The person must also "
        "be in the stage's approver group.",
        [("extras", "approvalworkflowstage")],
        ["view", "change"],
    ),
)


def ensure_default_permissions(sender=None, *, apps, **kwargs):  # pylint: disable=unused-argument
    """Create each default permission that is missing, matched by name. Returns the names created."""
    try:
        object_permission = apps.get_model("users", "ObjectPermission")
        content_type = apps.get_model("contenttypes", "ContentType")
    except LookupError:
        return []
    created = []
    for name, description, object_types, actions in DEFAULT_PERMISSIONS:
        if object_permission.objects.filter(name=name).exists():
            continue
        types = []
        for app_label, model in object_types:
            found = content_type.objects.filter(app_label=app_label, model=model).first()
            if found is not None:
                types.append(found)
        if len(types) != len(object_types):
            # For example the approval models on a Nautobot without approvals. Try again next migrate.
            continue
        permission = object_permission.objects.create(
            name=name, description=description, enabled=True, actions=list(actions)
        )
        permission.object_types.set(types)
        created.append(name)
    if created:
        logger.info("Created %s Workflow Engine permission(s) for admins to assign.", len(created))
    return created


def retire_old_permissions(apps, schema_editor=None):  # pylint: disable=unused-argument
    """Delete retired auto-created permissions that nobody changed or assigned.

    A retired permission is deleted only when it still has its original description,
    actions, object types, no constraints, is enabled, and has no users or groups.
    Anything else was touched by an admin, so it stays and a warning is logged.
    Returns ``(deleted, kept)`` lists of names.
    """
    object_permission = apps.get_model("users", "ObjectPermission")
    deleted, kept = [], []
    for name, description, object_types, actions in RETIRED_PERMISSIONS:
        permission = object_permission.objects.filter(name=name).first()
        if permission is None:
            continue
        types = sorted((ct.app_label, ct.model) for ct in permission.object_types.all())
        untouched = (
            permission.description == description
            and sorted(permission.actions or []) == sorted(actions)
            and types == sorted(object_types)
            and not permission.constraints
            and permission.enabled
        )
        assigned = permission.users.exists() or permission.groups.exists()
        if untouched and not assigned:
            permission.delete()
            deleted.append(name)
            continue
        kept.append(name)
        logger.warning(
            "Left the permission %r in place because it was %s. It is no longer created or needed: %s",
            name,
            "assigned to users or groups" if assigned else "changed by an admin",
            "templates and scripts are part of Build workflows now."
            if "Author" in name
            else "approvers come from Nautobot's approval workflow definitions.",
        )
    return deleted, kept
