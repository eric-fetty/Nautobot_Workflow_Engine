"""Run workflows covers seeing, launching and cancelling; View runs stays separate."""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from nautobot.users.models import ObjectPermission

from nautobot_workflow_engine.datasources import save_workflow_definition
from nautobot_workflow_engine.default_permissions import RUN_WORKFLOWS
from nautobot_workflow_engine.models import WorkflowRun
from nautobot_workflow_engine.run_permissions import can_cancel, can_run

User = get_user_model()


def _definition(key, category):
    return {
        "key": key,
        "name": key.replace("-", " ").title(),
        "description": "",
        "category": category,
        "approval_required": False,
        "inputs": [{"key": "reason", "label": "Reason", "type": "string"}],
        "actions": [{"type": "awx", "name": "push", "label": "Push", "template_id": 3}],
    }


def _copy(name, constraints=None, actions=None, model="workflow"):
    source = ObjectPermission.objects.get(name=name)
    copy = ObjectPermission.objects.create(
        name=f"{name} (copy {ObjectPermission.objects.count()})",
        actions=actions or list(source.actions),
        constraints=constraints,
        enabled=True,
    )
    copy.object_types.set([ContentType.objects.get(app_label="nautobot_workflow_engine", model=model)])
    return copy


class RunWorkflowsPermissionTestCase(TestCase):
    """A person who only holds Run workflows."""

    def setUp(self):
        self.network = save_workflow_definition(_definition("net-flow", "Networking"), source_repo=None)
        self.other = save_workflow_definition(_definition("other-flow", "Servers"), source_repo=None)
        for workflow in (self.network, self.other):
            workflow.enabled = True
            workflow.save()
        self.user = User.objects.create(username="run-only")
        ObjectPermission.objects.get(name=RUN_WORKFLOWS).users.add(self.user)
        self.client.force_login(self.user)

    def _launch(self, workflow):
        return reverse("plugins:nautobot_workflow_engine:workflow_launch", kwargs={"key": workflow.key})

    def test_ready_made_entry_covers_view_run_and_cancel_on_workflows(self):
        permission = ObjectPermission.objects.get(name=RUN_WORKFLOWS)
        self.assertEqual(sorted(permission.actions), ["cancel", "run", "view"])
        self.assertEqual([ct.model for ct in permission.object_types.all()], ["workflow"])

    def test_sees_the_list_and_the_launch_page(self):
        response = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Net Flow")
        self.assertContains(response, "Other Flow")
        response = self.client.get(self._launch(self.network))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Run workflow")

    def test_does_not_get_build_rights_or_every_run(self):
        self.assertFalse(self.user.has_perm("nautobot_workflow_engine.change_workflow"))
        self.assertFalse(self.user.has_perm("nautobot_workflow_engine.add_workflow"))
        self.assertFalse(self.user.has_perm("nautobot_workflow_engine.view_workflowrun"))
        response = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_builder_create"))
        self.assertEqual(response.status_code, 403)

    def test_a_constrained_copy_limits_seeing_running_and_cancelling(self):
        ObjectPermission.objects.get(name=RUN_WORKFLOWS).users.remove(self.user)
        user = User.objects.create(username="net-runner")
        _copy(RUN_WORKFLOWS, constraints={"category": "Networking"}).users.add(user)
        self.assertTrue(can_run(user, self.network))
        self.assertFalse(can_run(user, self.other))
        theirs = WorkflowRun.objects.create(workflow=self.other, requested_by=self.user, status="scheduled", inputs={})
        ours = WorkflowRun.objects.create(workflow=self.network, requested_by=self.user, status="scheduled", inputs={})
        self.assertTrue(can_cancel(user, ours))
        self.assertFalse(can_cancel(user, theirs))
        self.client.force_login(user)
        self.assertEqual(self.client.get(self._launch(self.network)).status_code, 200)
        self.assertEqual(self.client.get(self._launch(self.other)).status_code, 404)
        listing = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertContains(listing, "Net Flow")
        self.assertNotContains(listing, "Other Flow")

    def test_cancels_anyones_scheduled_run_of_a_covered_workflow(self):
        someone = User.objects.create(username="someone-else")
        run = WorkflowRun.objects.create(workflow=self.network, requested_by=someone, status="scheduled", inputs={})
        # The Cancel button lives on the run page, which needs View runs.
        ObjectPermission.objects.get(name="Workflow Engine: View runs").users.add(self.user)
        user = User.objects.get(pk=self.user.pk)
        self.client.force_login(user)
        page = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_run", kwargs={"pk": run.pk}))
        self.assertContains(page, "Cancel Run")
        self.client.post(reverse("plugins:nautobot_workflow_engine:workflow_run_cancel", kwargs={"pk": run.pk}))
        run.refresh_from_db()
        self.assertEqual(run.status, "canceled")

    def test_view_runs_alone_cannot_cancel(self):
        viewer = User.objects.create(username="viewer")
        ObjectPermission.objects.get(name="Workflow Engine: View runs").users.add(viewer)
        run = WorkflowRun.objects.create(workflow=self.network, requested_by=self.user, status="scheduled", inputs={})
        self.client.force_login(viewer)
        page = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_run", kwargs={"pk": run.pk}))
        self.assertNotContains(page, "Cancel Run")
        self.client.post(reverse("plugins:nautobot_workflow_engine:workflow_run_cancel", kwargs={"pk": run.pk}))
        run.refresh_from_db()
        self.assertEqual(run.status, "scheduled")

    def test_older_model_permissions_still_work(self):
        legacy = User.objects.create(username="legacy")
        for actions, model in ((["add", "change"], "workflowrun"), (["view"], "workflow")):
            permission = ObjectPermission.objects.create(name=f"legacy {model}", actions=actions, enabled=True)
            permission.object_types.set([ContentType.objects.get(app_label="nautobot_workflow_engine", model=model)])
            permission.users.add(legacy)
        run = WorkflowRun.objects.create(workflow=self.network, requested_by=self.user, status="scheduled", inputs={})
        self.assertTrue(can_run(legacy, self.network))
        self.assertTrue(can_cancel(legacy, run))
