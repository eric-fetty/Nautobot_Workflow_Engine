"""Builder API coverage for refresh, adopt, retry, archive, and permissions."""

import json
import subprocess
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from git.exc import GitCommandError
from rest_framework.test import APIClient

from nautobot_workflow_engine.models import GIT_STATUS_NOT_SYNCED, MANAGED_BY_GIT, Workflow, WorkflowRevision
from nautobot_workflow_engine.services import duplicate_workflow
from nautobot_workflow_engine.tests.git_support import TOKEN_VALUE, BareRepoMixin, grant
from nautobot_workflow_engine.utilities.git import WorkflowGitRepo

User = get_user_model()


class GitApiTestCase(BareRepoMixin, TestCase):
    """HTTP endpoints on top of the bare-repo push."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.other = User.objects.create(username="git-other")

    def _url(self, name, **kwargs):
        return reverse(f"plugins-api:nautobot_workflow_engine-api:{name}", kwargs=kwargs or None)

    def _send(self, method, name, body=None, **kwargs):
        url = self._url(name, **kwargs)
        client_method = getattr(self.client, method)
        if body is None:
            return client_method(url)
        return client_method(url, data=json.dumps(body), content_type="application/json")

    def test_status_refresh_retry_archive_and_duplicate(self):
        saved, revision = self.save_and_push(self.definition())
        status = self._send("get", "workflow-git-status", key=saved.key)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.data["git_status"], "in_sync")
        self.assertEqual(status.data["git_commit"], saved.git_commit)
        detail = self._send("get", "workflow-detail", key=saved.key)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["base"]["git_commit"], saved.git_commit)
        self.assertEqual(detail.data["base"]["git_content_hash"], saved.git_content_hash)
        listed = self._send("get", "workflow-list")
        self.assertEqual(listed.status_code, 200)
        row = next(item for item in listed.data if item["key"] == saved.key)
        self.assertEqual(row["git_status"], "in_sync")

        self.remote_commit({"README.md": "later\n"}, "readme")
        refreshed = self._send("post", "workflow-git-refresh", key=saved.key)
        self.assertEqual(refreshed.status_code, 200)
        self.assertIn(refreshed.data["state"], ("current", "syncing"), refreshed.data)
        saved.refresh_from_db()
        self.assertEqual(saved.name, "Lab flow")

        saved.git_status = GIT_STATUS_NOT_SYNCED
        saved.git_error = "E-NWE-203 earlier"
        saved.save(update_fields=["git_status", "git_error", "last_updated"])
        retried = self._send("post", "workflow-git-push", key=saved.key)
        self.assertEqual(retried.status_code, 202)
        self.assertEqual(retried.data["git_status"], "in_sync", retried.data)
        self.assertNotIn(TOKEN_VALUE, json.dumps(retried.data))

        archived = self._send("post", "workflow-archive", {"confirm_key": saved.key}, key=saved.key)
        self.assertEqual(archived.status_code, 200)
        saved.refresh_from_db()
        self.assertTrue(saved.archived)
        publish_now = self._send("post", "workflow-git-push", key=saved.key)
        self.assertEqual(publish_now.status_code, 202)
        with self.assertRaises(subprocess.CalledProcessError):
            self.show_file(saved.git_path)
        restored = self._send("post", "workflow-restore", key=saved.key)
        self.assertEqual(restored.status_code, 200)
        saved.refresh_from_db()
        self.assertFalse(saved.archived)
        self._send("post", "workflow-git-push", key=saved.key)
        self.assertIn("key: lab-flow", self.show_file("workflows/lab-flow.yaml"))

        copy, draft = duplicate_workflow(user=self.user, source=saved, key="lab-copy", name="Lab copy")
        self.assertEqual(copy.managed_by, "nautobot")
        self.assertFalse(copy.enabled)
        self.assertEqual(copy.raw_definition, {})
        self.assertEqual(draft.state, WorkflowRevision.STATE_DRAFT)
        self.assertEqual(revision.state, WorkflowRevision.STATE_PUBLISHED)

    def test_adopt_include_warning_and_readonly_repository(self):
        self.remote_commit(
            {
                "workflows/adopted-flow.yaml": (
                    "key: adopted-flow\nname: Adopted\n{% include 'extra.yaml' %}\n"
                    "actions:\n  - type: awx\n    name: run\n    template_id: 3\n"
                ),
                "workflows/extra.yaml": "category: lab\n",
            },
            "seed",
        )
        self.pull_into_database()
        preview = self._send("post", "workflow-adopt", {"confirm": False}, key="adopted-flow")
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data["adopted"])
        self.assertIn("extra.yaml", preview.data["includes"])
        self.assertIn("canonical form", preview.data["warning"])
        self.assertEqual(Workflow.objects.get(key="adopted-flow").managed_by, MANAGED_BY_GIT)
        adopted = self._send("post", "workflow-adopt", {"confirm": True}, key="adopted-flow")
        self.assertEqual(adopted.status_code, 200)
        self.assertTrue(adopted.data["adopted"])
        self.assertEqual(Workflow.objects.get(key="adopted-flow").managed_by, "nautobot")

        readonly = self.make_repository(publishable=False)
        grant(self.user, ["view"], type(readonly))
        saved, revision = self.save_and_push(self.definition(key="keep-me", name="Keep"))
        rejected = self._send(
            "post",
            "workflow-publish",
            {
                "definition": self.definition(key="keep-me", name="Should not stick"),
                "base_revision": revision.number,
                "repository": str(readonly.pk),
                "enable": False,
            },
            key="keep-me",
        )
        self.assertEqual(rejected.status_code, 400)
        saved.refresh_from_db()
        self.assertEqual(saved.name, "Keep")

    def test_permission_matrix(self):
        saved, _revision = self.save_and_push(self.definition(key="guarded"))
        stranger = APIClient()
        self.assertEqual(stranger.get(self._url("workflow-git-status", key=saved.key)).status_code, 403)
        self.client.force_authenticate(self.other)
        self.assertEqual(self._send("get", "workflow-git-status", key=saved.key).status_code, 403)
        self.assertEqual(self._send("post", "workflow-git-push", key=saved.key).status_code, 403)
        self.assertEqual(
            self._send("post", "workflow-archive", {"confirm_key": saved.key}, key=saved.key).status_code, 403
        )
        grant(self.other, ["view"], Workflow, constraints={"key": "someone-else"})
        self.assertEqual(self._send("get", "workflow-git-status", key=saved.key).status_code, 404)
        grant(self.other, ["publish"], Workflow, constraints={"key": "someone-else"})
        self.assertEqual(self._send("post", "workflow-git-push", key=saved.key).status_code, 404)
        grant(self.other, ["delete"], Workflow, constraints={"key": "someone-else"})
        self.assertEqual(
            self._send("post", "workflow-archive", {"confirm_key": saved.key}, key=saved.key).status_code, 404
        )
        grant(self.other, ["change"], Workflow, constraints={"key": "someone-else"})
        self.assertEqual(self._send("post", "workflow-restore", key=saved.key).status_code, 404)
        self.assertEqual(self._send("post", "workflow-adopt", {"confirm": True}, key=saved.key).status_code, 404)


class GitPushResponseTestCase(BareRepoMixin, TransactionTestCase):
    """The publish response reports a failed push without rolling back the save."""

    def test_failed_push_is_visible_and_keeps_the_row(self):
        saved, revision = self.save_and_push(self.definition(key="api-flow", name="API flow"))
        client = APIClient()
        client.force_authenticate(self.user)
        message = f"authentication failed for {TOKEN_VALUE}"
        url = reverse("plugins-api:nautobot_workflow_engine-api:workflow-publish", kwargs={"key": saved.key})
        push_url = reverse("plugins-api:nautobot_workflow_engine-api:workflow-git-push", kwargs={"key": saved.key})
        with patch.object(WorkflowGitRepo, "push_once", side_effect=GitCommandError(["git", "push"], message)):
            response = client.post(
                url,
                data=json.dumps(
                    {
                        "definition": self.definition(key="api-flow", name="Saved anyway"),
                        "base_revision": revision.number,
                        "enable": False,
                        "commit_summary": "Keep the row",
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 202, getattr(response, "data", response.content))
            self.assertNotIn(TOKEN_VALUE, response.content.decode())
            retry = client.post(push_url)
        self.assertEqual(retry.status_code, 202, getattr(retry, "data", retry.content))
        self.assertEqual(retry.data["git_status"], "not_synced")
        self.assertNotIn(TOKEN_VALUE, retry.content.decode())
        saved.refresh_from_db()
        self.assertEqual(saved.name, "Saved anyway")
        self.assertEqual(saved.git_status, "not_synced")
        self.assertNotIn(TOKEN_VALUE, saved.git_error)
