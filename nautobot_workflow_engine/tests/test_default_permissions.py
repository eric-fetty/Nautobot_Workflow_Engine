"""Permissions the app creates on install: present, correct, idempotent, and never assigned."""

from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.users.models import ObjectPermission

from nautobot_workflow_engine.default_permissions import DEFAULT_PERMISSIONS, ensure_default_permissions

NAMES = [row[0] for row in DEFAULT_PERMISSIONS]


class DefaultPermissionsTestCase(TestCase):
    """The migrate hook has already run on the test database."""

    def test_every_documented_permission_is_created(self):
        expected = {
            "Workflow Engine: View workflows": ("workflow", ["view"]),
            "Workflow Engine: Run workflows": ("workflowrun", ["add"]),
            "Workflow Engine: View runs": ("workflowrun", ["view"]),
            "Workflow Engine: Cancel runs": ("workflowrun", ["change"]),
            "Workflow Engine: Build workflows": ("workflow", ["view", "add", "change"]),
            "Workflow Engine: Publish workflows": ("workflow", ["publish"]),
            "Workflow Engine: Author templates and scripts": ("workflow", ["author_code"]),
            "Workflow Engine: Write scripts": ("workflow", ["write_script"]),
            "Workflow Engine: Approve runs": ("approvalworkflowstage", ["view", "change"]),
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
