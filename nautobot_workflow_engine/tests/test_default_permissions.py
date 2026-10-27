"""Permissions the app creates on install: present, correct, idempotent, and never assigned."""

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.users.models import ObjectPermission

from nautobot_workflow_engine.default_permissions import (
    DEFAULT_PERMISSIONS,
    OLD_RUN_WORKFLOWS,
    RETIRED_PERMISSIONS,
    RETIRED_WITH_RUN_WORKFLOWS,
    RUN_WORKFLOWS,
    ensure_default_permissions,
    fold_view_and_cancel_into_run,
    retire_old_permissions,
    upgrade_run_workflows,
)

NAMES = [row[0] for row in DEFAULT_PERMISSIONS]


class DefaultPermissionsTestCase(TestCase):
    """The migrate hook has already run on the test database."""

    def test_every_documented_permission_is_created(self):
        expected = {
            "Workflow Engine: Run workflows": ("workflow", ["view", "run", "cancel"]),
            "Workflow Engine: View runs": ("workflowrun", ["view"]),
            "Workflow Engine: Build workflows": ("workflow", ["view", "add", "change"]),
            "Workflow Engine: Publish workflows": ("workflow", ["publish"]),
            "Workflow Engine: Write scripts": ("workflow", ["write_script"]),
        }
        self.assertEqual(set(NAMES), set(expected))
        for name, (model, actions) in expected.items():
            permission = ObjectPermission.objects.get(name=name)
            self.assertEqual(sorted(permission.actions), sorted(actions), name)
            self.assertEqual([ct.model for ct in permission.object_types.all()], [model], name)
            self.assertTrue(permission.enabled)
            self.assertTrue(permission.description)
            self.assertIsNone(permission.constraints)

    def test_nobody_is_assigned(self):
        for permission in ObjectPermission.objects.filter(name__in=NAMES):
            self.assertFalse(permission.users.exists(), permission.name)
            self.assertFalse(permission.groups.exists(), permission.name)

    def test_running_again_creates_nothing(self):
        before = ObjectPermission.objects.filter(name__in=NAMES).count()
        self.assertEqual(ensure_default_permissions(apps=django_apps), [])
        self.assertEqual(ObjectPermission.objects.filter(name__in=NAMES).count(), before)

    def test_admin_edits_are_kept(self):
        permission = ObjectPermission.objects.get(name="Workflow Engine: Publish workflows")
        permission.actions = ["publish", "view"]
        permission.constraints = {"category": "Networking"}
        permission.description = "Ours"
        permission.save()
        ensure_default_permissions(apps=django_apps)
        permission.refresh_from_db()
        self.assertEqual(permission.actions, ["publish", "view"])
        self.assertEqual(permission.constraints, {"category": "Networking"})
        self.assertEqual(permission.description, "Ours")

    def test_a_deleted_one_comes_back(self):
        ObjectPermission.objects.filter(name="Workflow Engine: Write scripts").delete()
        self.assertEqual(ensure_default_permissions(apps=django_apps), ["Workflow Engine: Write scripts"])
        permission = ObjectPermission.objects.get(name="Workflow Engine: Write scripts")
        self.assertEqual(permission.actions, ["write_script"])
        self.assertEqual(
            list(permission.object_types.all()),
            [ContentType.objects.get(app_label="nautobot_workflow_engine", model="workflow")],
        )
        self.assertFalse(permission.users.exists())


RETIRED = {row[0]: row for row in RETIRED_PERMISSIONS + RETIRED_WITH_RUN_WORKFLOWS + (OLD_RUN_WORKFLOWS,)}
AUTHOR = "Workflow Engine: Author templates and scripts"
APPROVE = "Workflow Engine: Approve runs"


class RetiredPermissionsTestCase(TestCase):
    """The two entries earlier builds created are removed only when nobody touched them."""

    def _recreate(self, name):
        _name, description, object_types, actions = RETIRED[name]
        permission = ObjectPermission.objects.create(name=name, description=description, actions=list(actions))
        permission.object_types.set(
            [ContentType.objects.get(app_label=app_label, model=model) for app_label, model in object_types]
        )
        return permission

    def test_they_are_not_created_any_more(self):
        self.assertNotIn(AUTHOR, NAMES)
        self.assertNotIn(APPROVE, NAMES)
        ensure_default_permissions(apps=django_apps)
        self.assertFalse(ObjectPermission.objects.filter(name__in=[AUTHOR, APPROVE]).exists())

    def test_untouched_unassigned_copies_are_deleted(self):
        self._recreate(AUTHOR)
        self._recreate(APPROVE)
        deleted, kept = retire_old_permissions(django_apps)
        self.assertEqual(sorted(deleted), sorted([AUTHOR, APPROVE]))
        self.assertEqual(kept, [])
        self.assertFalse(ObjectPermission.objects.filter(name__in=[AUTHOR, APPROVE]).exists())

    def test_assigned_or_edited_copies_stay_with_a_warning(self):
        user = get_user_model().objects.create(username="still-uses-it")
        self._recreate(AUTHOR).users.add(user)
        edited = self._recreate(APPROVE)
        edited.constraints = {"name": "x"}
        edited.save()
        with self.assertLogs("nautobot_workflow_engine.default_permissions", level="WARNING") as logs:
            deleted, kept = retire_old_permissions(django_apps)
        self.assertEqual(deleted, [])
        self.assertEqual(sorted(kept), sorted([AUTHOR, APPROVE]))
        self.assertTrue(ObjectPermission.objects.filter(name=AUTHOR, users=user).exists())
        self.assertTrue(ObjectPermission.objects.filter(name=APPROVE).exists())
        self.assertTrue(any("assigned to users or groups" in line for line in logs.output))
        self.assertTrue(any("changed by an admin" in line for line in logs.output))

    def test_a_changed_description_or_group_also_keeps_it(self):
        from django.contrib.auth.models import Group

        renamed = self._recreate(AUTHOR)
        renamed.description = "Ours"
        renamed.save()
        self._recreate(APPROVE).groups.add(Group.objects.create(name="approvers"))
        with self.assertLogs("nautobot_workflow_engine.default_permissions", level="WARNING"):
            deleted, _kept = retire_old_permissions(django_apps)
        self.assertEqual(deleted, [])

    def test_the_migration_step_runs_cleanly_when_there_is_nothing_to_do(self):
        self.assertEqual(retire_old_permissions(django_apps), ([], []))


VIEW_WORKFLOWS = "Workflow Engine: View workflows"
CANCEL_RUNS = "Workflow Engine: Cancel runs"


class FoldIntoRunWorkflowsTestCase(TestCase):
    """Migration 0014: View workflows and Cancel runs become part of Run workflows."""

    _recreate = RetiredPermissionsTestCase._recreate

    def setUp(self):
        ObjectPermission.objects.filter(name=RUN_WORKFLOWS).delete()

    def test_untouched_entries_are_folded(self):
        user = get_user_model().objects.create(username="operator")
        self._recreate(RUN_WORKFLOWS).users.add(user)
        self._recreate(VIEW_WORKFLOWS)
        self._recreate(CANCEL_RUNS)
        deleted, kept = fold_view_and_cancel_into_run(django_apps)
        self.assertEqual(sorted(deleted), sorted([VIEW_WORKFLOWS, CANCEL_RUNS]))
        self.assertEqual(kept, [])
        run = ObjectPermission.objects.get(name=RUN_WORKFLOWS)
        self.assertEqual(sorted(run.actions), ["cancel", "run", "view"])
        self.assertEqual([ct.model for ct in run.object_types.all()], ["workflow"])
        self.assertTrue(run.users.filter(pk=user.pk).exists(), "assignments are kept")
        self.assertFalse(upgrade_run_workflows(django_apps), "running it again changes nothing")

    def test_they_are_not_created_again(self):
        ensure_default_permissions(apps=django_apps)
        self.assertFalse(ObjectPermission.objects.filter(name__in=[VIEW_WORKFLOWS, CANCEL_RUNS]).exists())

    def test_an_edited_run_workflows_is_left_with_a_warning(self):
        run = self._recreate(RUN_WORKFLOWS)
        run.constraints = {"requested_by": "x"}
        run.save()
        with self.assertLogs("nautobot_workflow_engine.default_permissions", level="WARNING") as logs:
            self.assertFalse(upgrade_run_workflows(django_apps))
        run.refresh_from_db()
        self.assertEqual(run.actions, ["add"])
        self.assertTrue(any("changed by an admin" in line for line in logs.output))

    def test_a_scoped_view_is_not_widened(self):
        user = get_user_model().objects.create(username="net-operator")
        self._recreate(RUN_WORKFLOWS).users.add(user)
        scoped = self._recreate(VIEW_WORKFLOWS)
        scoped.name = "Networking view"
        scoped.constraints = {"category": "Networking"}
        scoped.save()
        scoped.users.add(user)
        with self.assertLogs("nautobot_workflow_engine.default_permissions", level="WARNING") as logs:
            self.assertFalse(upgrade_run_workflows(django_apps))
        self.assertEqual(ObjectPermission.objects.get(name=RUN_WORKFLOWS).actions, ["add"])
        self.assertTrue(any("constrained view" in line for line in logs.output))

    def test_assigned_view_and_cancel_entries_stay_with_a_warning(self):
        user = get_user_model().objects.create(username="kept")
        self._recreate(VIEW_WORKFLOWS).users.add(user)
        self._recreate(CANCEL_RUNS).users.add(user)
        with self.assertLogs("nautobot_workflow_engine.default_permissions", level="WARNING"):
            deleted, kept = retire_old_permissions(django_apps, retired=RETIRED_WITH_RUN_WORKFLOWS)
        self.assertEqual(deleted, [])
        self.assertEqual(sorted(kept), sorted([VIEW_WORKFLOWS, CANCEL_RUNS]))


class FreshDatabaseTestCase(TestCase):
    """On a fresh database the permissions come from this app's own post_migrate."""

    def test_this_apps_post_migrate_creates_them(self):
        from django.db.models.signals import post_migrate

        config = django_apps.get_app_config("nautobot_workflow_engine")
        ObjectPermission.objects.filter(name__in=NAMES).delete()
        post_migrate.send(
            sender=config, app_config=config, verbosity=0, interactive=False, using="default", apps=django_apps, plan=[]
        )
        self.assertEqual(
            set(ObjectPermission.objects.filter(name__in=NAMES).values_list("name", flat=True)), set(NAMES)
        )
