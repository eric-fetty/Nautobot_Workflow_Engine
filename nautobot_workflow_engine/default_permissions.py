"""Object permissions the app creates on install, ready for an admin to assign.

They are created on every ``nautobot-server migrate`` / ``post_upgrade`` (via
``nautobot_database_ready``) only when no permission with the same name exists, so an
admin's edits, renames of actions or constraints, and assignments are never touched.
No users or groups are ever assigned here.
"""

import logging

logger = logging.getLogger(__name__)

APP = "nautobot_workflow_engine"

RUN_WORKFLOWS = "Workflow Engine: Run workflows"

# (name, description, [(app_label, model)], [actions])
DEFAULT_PERMISSIONS = (
    (
        RUN_WORKFLOWS,
        "See workflows on the Workflows list, open their launch page, start runs, and cancel a run that is "
        'still Scheduled or Pending Approval. Add a constraint, for example {"category": "Networking"}, '
        "to limit which workflows.",
        [(APP, "workflow")],
        ["view", "run", "cancel"],
    ),
    (
        "Workflow Engine: View runs",
        "See runs in Run History, their details, and the dashboard figures built from them.",
        [(APP, "workflowrun")],
        ["view"],
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
# approvers come from Nautobot's own approval workflow definitions. Migration 0013.
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


# Migration 0014: "View workflows" and "Cancel runs" are part of "Run workflows" now.
RETIRED_WITH_RUN_WORKFLOWS = (
    (
        "Workflow Engine: View workflows",
        "See published workflows on the Workflows list and open their launch page. Add a constraint, "
        'for example {"category": "Networking"}, to limit which workflows.',
        [(APP, "workflow")],
        ["view"],
    ),
    (
        "Workflow Engine: Cancel runs",
        "Cancel a run that is still Scheduled or Pending Approval.",
        [(APP, "workflowrun")],
        ["change"],
    ),
)

# "Run workflows" exactly as earlier builds created it.
OLD_RUN_WORKFLOWS = (
    RUN_WORKFLOWS,
    "Start runs. Pair it with “View workflows” to control which workflows a person can run.",
    [(APP, "workflowrun")],
    ["add"],
)

_WHY_RETIRED = {
    "Workflow Engine: Author templates and scripts": "templates and scripts are part of Build workflows now.",
    "Workflow Engine: Approve runs": "approvers come from Nautobot's approval workflow definitions.",
    "Workflow Engine: View workflows": "Run workflows and Build workflows include seeing workflows now.",
    "Workflow Engine: Cancel runs": "Run workflows includes cancelling runs now.",
}


def ensure_default_permissions(sender=None, *, apps=None, **kwargs):  # pylint: disable=unused-argument
    """Create each default permission that is missing, matched by name. Returns the names created."""
    if apps is None:
        # Some post_migrate senders (for example a test flush) don't pass the migration state.
        from django.apps import apps as global_apps

        apps = global_apps
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


def _untouched(permission, definition):
    """True when ``permission`` still matches how the app created it."""
    _name, description, object_types, actions = definition
    types = sorted((ct.app_label, ct.model) for ct in permission.object_types.all())
    return (
        permission.description == description
        and sorted(permission.actions or []) == sorted(actions)
        and types == sorted(object_types)
        and not permission.constraints
        and permission.enabled
    )


def retire_old_permissions(apps, schema_editor=None, retired=RETIRED_PERMISSIONS):  # pylint: disable=unused-argument
    """Delete retired auto-created permissions that nobody changed or assigned.

    A retired permission is deleted only when it still has its original description,
    actions, object types, no constraints, is enabled, and has no users or groups.
    Anything else was touched by an admin, so it stays and a warning is logged.
    Returns ``(deleted, kept)`` lists of names.
    """
    object_permission = apps.get_model("users", "ObjectPermission")
    deleted, kept = [], []
    for definition in retired:
        name = definition[0]
        permission = object_permission.objects.filter(name=name).first()
        if permission is None:
            continue
        untouched = _untouched(permission, definition)
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
            _WHY_RETIRED.get(name, ""),
        )
    return deleted, kept


def _relies_on_a_scoped_view(object_permission, permission):
    """True when someone on ``permission`` also has a constrained view of workflows.

    They were limited to some workflows by that constraint. Giving the unconstrained
    Run workflows entry ``view`` would let them see and run every workflow.
    """
    users = list(permission.users.all())
    groups = list(permission.groups.all())
    if not users and not groups:
        return False
    user_ids = [user.pk for user in users]
    group_ids = [group.pk for group in groups]
    candidates = object_permission.objects.filter(object_types__app_label=APP, object_types__model="workflow")
    for other in candidates.exclude(pk=permission.pk).distinct():
        if not other.constraints or "view" not in (other.actions or []):
            continue
        if other.users.filter(pk__in=user_ids).exists() or other.groups.filter(pk__in=group_ids).exists():
            return True
    return False


def upgrade_run_workflows(apps, schema_editor=None):  # pylint: disable=unused-argument
    """Give the auto-created Run workflows entry its new object type and actions.

    Only an entry that still matches how earlier builds created it is changed, and only
    when nobody on it depends on a constrained view of workflows. Its users and groups
    stay. Anything else is left alone with a warning. Returns True when it was changed.
    """
    object_permission = apps.get_model("users", "ObjectPermission")
    content_type = apps.get_model("contenttypes", "ContentType")
    permission = object_permission.objects.filter(name=RUN_WORKFLOWS).first()
    if permission is None:
        return False
    new = next(row for row in DEFAULT_PERMISSIONS if row[0] == RUN_WORKFLOWS)
    if _untouched(permission, new):
        return False
    reason = None
    if not _untouched(permission, OLD_RUN_WORKFLOWS):
        reason = "it was changed by an admin"
    elif _relies_on_a_scoped_view(object_permission, permission):
        reason = "someone on it has a constrained view of workflows, and the new entry would widen that"
    if reason is not None:
        logger.warning(
            "Left the permission %r as it is because %s. Give the people on it the actions "
            "view, run and cancel on workflow yourself.",
            RUN_WORKFLOWS,
            reason,
        )
        return False
    workflow_type = content_type.objects.filter(app_label=APP, model="workflow").first()
    if workflow_type is None:
        return False
    _name, description, _types, actions = new
    permission.description = description
    permission.actions = list(actions)
    permission.save()
    permission.object_types.set([workflow_type])
    logger.info("Updated %r: it now covers seeing, running and cancelling workflows.", RUN_WORKFLOWS)
    return True


def fold_view_and_cancel_into_run(apps, schema_editor=None):
    """Migration 0014: update Run workflows, then retire View workflows and Cancel runs."""
    upgrade_run_workflows(apps, schema_editor)
    return retire_old_permissions(apps, schema_editor, retired=RETIRED_WITH_RUN_WORKFLOWS)
