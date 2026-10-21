"""Permission, ownership, and error-path tests for the workflow builder."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from nautobot.extras.models import GitRepository, ObjectChange
from nautobot.users.models import ObjectPermission

from nautobot_workflow_engine.models import MANAGED_BY_GIT, MANAGED_BY_NAUTOBOT, Workflow
from nautobot_workflow_engine.schema.builder_payload import builder_from_definition, definition_from_builder

User = get_user_model()
_PERM_SEQ = 0
ESCAPE = "{{ cycler.__init__.__globals__.os.popen('id').read() }}"


def grant(user, actions, model, constraints=None):
    """Grant one object permission and attach it to ``user``."""
    global _PERM_SEQ
    _PERM_SEQ += 1
    permission = ObjectPermission.objects.create(
        name=f"builder-{_PERM_SEQ}-{user.username}-{'-'.join(actions)}",
        actions=actions,
        constraints=constraints,
        enabled=True,
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


def _payload(**overrides):
    payload = {
        "key": "lab-flow",
        "name": "Lab flow",
        "description": "",
        "category": "lab",
        "enabled": False,
        "approval_required": False,
        "inputs": [{"key": "hostname", "label": "Hostname", "type": "string"}],
        "actions": {"pre": [], "main": [{"type": "awx", "name": "run", "template_id": 3}], "post": []},
        "schedule": {"enabled": False},
    }
    payload.update(overrides)
    return payload


class BuilderPermissionTestCase(TestCase):
    """Create, edit, and object-scope checks on the builder endpoints."""

    def setUp(self):
        self.user = User.objects.create(username="builder")
        self.create_url = reverse("plugins:nautobot_workflow_engine:workflow_builder_create")
        self.list_url = reverse("plugins:nautobot_workflow_engine:workflows_list")

    def _login(self):
        self.client.force_login(self.user)

    def _store(self, **overrides):
        payload = _payload(**overrides)
        definition = definition_from_builder(payload)
        return Workflow.objects.create(
            key=definition["key"],
            name=definition["name"],
            category=definition.get("category") or "",
            enabled=definition.get("enabled", False),
            approval_required=definition.get("approval_required", False),
            raw_definition=definition,
            managed_by=MANAGED_BY_NAUTOBOT,
        )

    def _post(self, url, payload):
        return self.client.post(url, {"payload": json.dumps(payload)})

    def test_anonymous_and_unprivileged_list_are_forbidden(self):
        anonymous = self.client.get(self.list_url)
        self.assertEqual(anonymous.status_code, 403)
        self._login()
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 403)

    def test_create_requires_add(self):
        grant(self.user, ["view", "change"], Workflow)
        self._login()
        response = self._post(self.create_url, _payload(key="new-flow"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Workflow.objects.filter(key="new-flow").exists())

    def test_create_with_add_is_change_logged(self):
        grant(self.user, ["add", "view", "change"], Workflow)
        self._login()
        response = self._post(self.create_url, _payload(key="new-flow", name="New flow"))
        self.assertEqual(response.status_code, 302)
        workflow = Workflow.objects.get(key="new-flow")
        self.assertEqual(workflow.managed_by, MANAGED_BY_NAUTOBOT)
        self.assertFalse(workflow.enabled)
        self.assertTrue(ObjectChange.objects.filter(changed_object_id=workflow.pk, user=self.user).exists())

    def test_label_change_without_author_code_is_allowed(self):
        workflow = self._store()
        grant(self.user, ["view", "change"], Workflow, constraints={"category": "lab"})
        self._login()
        payload = builder_from_definition(workflow.raw_definition)
        payload["inputs"][0]["label"] = "Device name"
        response = self._post(
            reverse("plugins:nautobot_workflow_engine:workflow_builder_edit", kwargs={"key": workflow.key}),
            payload,
        )
        self.assertEqual(response.status_code, 302)
        workflow.refresh_from_db()
        self.assertEqual(workflow.raw_definition["inputs"][0]["label"], "Device name")

    def test_unsafe_template_requires_author_code(self):
        workflow = self._store()
        grant(self.user, ["view", "change"], Workflow, constraints={"category": "lab"})
        self._login()
        payload = builder_from_definition(workflow.raw_definition)
        payload["actions"]["main"][0]["when"] = "{{ inputs.hostname | upper }}"
        url = reverse("plugins:nautobot_workflow_engine:workflow_builder_edit", kwargs={"key": workflow.key})
        response = self._post(url, payload)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Author templates and scripts", response.content.decode())
        workflow.refresh_from_db()
        self.assertNotIn("when", workflow.raw_definition["actions"][0])

        grant(self.user, ["author_code"], Workflow, constraints={"category": "lab"})
        response = self._post(url, payload)
        self.assertEqual(response.status_code, 302)
        workflow.refresh_from_db()
        self.assertEqual(workflow.raw_definition["actions"][0]["when"], "{{ inputs.hostname | upper }}")

    def test_category_outside_scope_rolls_back(self):
        workflow = self._store()
        grant(self.user, ["view", "change", "author_code", "publish"], Workflow, constraints={"category": "lab"})
        self._login()
        payload = builder_from_definition(workflow.raw_definition)
        payload["category"] = "prod"
        before = ObjectChange.objects.count()
        response = self._post(
            reverse("plugins:nautobot_workflow_engine:workflow_builder_edit", kwargs={"key": workflow.key}),
            payload,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("outside what you", response.content.decode())
        self.assertIn("allowed to edit", response.content.decode())
        workflow.refresh_from_db()
        self.assertEqual(workflow.category, "lab")
        self.assertEqual(ObjectChange.objects.count(), before)

    def test_out_of_scope_edit_is_404_before_403(self):
        workflow = self._store(key="prod-flow", category="prod")
        grant(self.user, ["view", "change"], Workflow, constraints={"category": "lab"})
        self._login()
        response = self.client.get(
            reverse("plugins:nautobot_workflow_engine:workflow_builder_edit", kwargs={"key": workflow.key})
        )
        self.assertEqual(response.status_code, 404)

    def test_repository_picker_is_restricted(self):
        GitRepository.objects.create(name="Visible Repo", remote_url="https://example.com/visible.git")
        GitRepository.objects.create(name="Hidden Repo", remote_url="https://example.com/hidden.git")
        grant(self.user, ["add", "view"], Workflow)
        grant(self.user, ["view"], GitRepository, constraints={"name": "Visible Repo"})
        self._login()
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Visible Repo", body)
        self.assertNotIn("Hidden Repo", body)

    def test_git_managed_builder_is_read_only(self):
        workflow = self._store()
        repo = GitRepository.objects.create(name="Git Repo", remote_url="https://example.com/git.git")
        workflow.managed_by = MANAGED_BY_GIT
        workflow.source_repo = repo
        workflow.save()
        grant(self.user, ["view", "change", "add"], Workflow)
        self._login()
        url = reverse("plugins:nautobot_workflow_engine:workflow_builder_edit", kwargs={"key": workflow.key})
        page = self.client.get(url)
        self.assertContains(page, "Managed in Git")
        response = self._post(url, builder_from_definition(workflow.raw_definition))
        self.assertEqual(response.status_code, 409)
        self.assertIn("Managed in Git", response.content.decode())
        duplicate = self.client.post(
            reverse("plugins:nautobot_workflow_engine:workflow_builder_duplicate", kwargs={"key": workflow.key})
        )
        self.assertEqual(duplicate.status_code, 302)
        copied = Workflow.objects.get(key="lab-flow-copy")
        self.assertEqual(copied.managed_by, MANAGED_BY_NAUTOBOT)
        self.assertFalse(copied.enabled)
        self.assertEqual(copied.source_repo_id, repo.id)

    def test_invalid_payloads_do_not_500(self):
        grant(self.user, ["add", "view", "change"], Workflow)
        self._login()
        binary = self.client.post(self.create_url, data=b"\xff", content_type="application/json")
        self.assertEqual(binary.status_code, 400)
        self.assertIn("not valid text", binary.content.decode())

        response = self._post(self.create_url, _payload(key="bad-repo", source_repo="not-a-uuid"))
        self.assertLess(response.status_code, 500)
        self.assertIn("repository", response.content.decode().lower())
        self.assertFalse(Workflow.objects.filter(key="bad-repo").exists())

        with patch("os.popen", side_effect=AssertionError("command ran")):
            escaped = self._post(
                self.create_url,
                _payload(
                    key="escaped",
                    actions={
                        "main": [{"type": "awx", "name": "run", "template_id": 1, "when": ESCAPE}],
                    },
                ),
            )
        self.assertLess(escaped.status_code, 500)
        self.assertIn("rejected", escaped.content.decode().lower())
        self.assertFalse(Workflow.objects.filter(key="escaped").exists())
