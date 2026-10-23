"""Publish failures say why in plain words, and the full error stays in the server log."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from git.exc import GitCommandError
from nautobot.core.utils.git import BranchDoesNotExist
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import GitRepository, Secret, SecretsGroup, SecretsGroupAssociation
from nautobot.extras.secrets.exceptions import SecretError
from rest_framework.test import APIClient

from nautobot_workflow_engine.models import (
    GIT_STATUS_DISABLED_REPO,
    GIT_STATUS_IN_SYNC,
    GIT_STATUS_NOT_SYNCED,
    WORKFLOW_PUBLISH_CONTENT,
    WORKFLOWS_CONTENT,
)
from nautobot_workflow_engine.tests.git_support import TOKEN_VALUE, BareRepoMixin
from nautobot_workflow_engine.utilities.git import NonFastForward, WorkflowGitRepo
from nautobot_workflow_engine.utilities.git_publish import publish_blocker, readable_failure, repo_can_publish

REPO = SimpleNamespace(name="netops-workflows", branch="main", secrets_group=None)


def git_error(stderr):
    """A GitCommandError shaped like the ones GitPython raises for ``git push``."""
    return GitCommandError(["git", "push", "origin", "HEAD:refs/heads/main"], 128, stderr=stderr)


class ReadableFailureTestCase(SimpleTestCase):
    """Each kind of Git failure maps to one plain sentence that names the repository."""

    def assertSays(self, exc, *phrases):
        message = readable_failure(exc, REPO)
        self.assertTrue(message.startswith("Couldn't push to netops-workflows"), message)
        for phrase in phrases:
            self.assertIn(phrase, message)
        self.assertNotIn("E-NWE", message)
        self.assertNotIn("Cmd(", message)
        return message

    def test_authentication_failed(self):
        self.assertSays(
            git_error("fatal: Authentication failed for 'https://git.example.net/x.git/'"), "authentication failed"
        )
        self.assertSays(
            git_error("fatal: could not read Password for 'https://****@git.example.net': terminal prompts disabled"),
            "authentication failed",
            "Username secret",
        )
        self.assertSays(
            git_error("remote: HTTP Basic: Access denied\nfatal: ... returned error: 401"), "authentication failed"
        )

    def test_protected_branch(self):
        self.assertSays(
            git_error(
                "remote: error: GH006: Protected branch update failed for refs/heads/main.\n ! [remote rejected]"
            ),
            "“main” is protected",
        )
        self.assertSays(git_error(" ! [remote rejected] HEAD -> main (pre-receive hook declined)"), "is protected")
        self.assertSays(
            git_error("remote: GitLab: You are not allowed to push code to protected branches"), "is protected"
        )

    def test_token_cannot_write(self):
        self.assertSays(
            git_error(
                "remote: Permission to netops/workflows.git denied to bot.\nfatal: ... The requested URL returned error: 403"
            ),
            "can't push to it",
        )
        self.assertSays(git_error("remote: Write access to repository not granted."), "can't push to it")

    def test_repository_not_found(self):
        self.assertSays(
            git_error("remote: Repository not found.\nfatal: repository 'https://x' not found"), "wasn't found"
        )

    def test_server_unreachable(self):
        self.assertSays(
            git_error("fatal: unable to access 'https://x/': Could not resolve host: git.example.net"), "couldn't reach"
        )

    def test_branch_missing_and_non_fast_forward(self):
        self.assertSays(BranchDoesNotExist("main does not exist"), "“main” doesn't exist")
        self.assertSays(NonFastForward("! [rejected] HEAD -> main (fetch first)"), "kept pushing to “main”")

    def test_secret_cannot_be_read(self):
        self.assertSays(SecretError(None, "environment-variable", "variable not set"), "couldn't be read")

    def test_anything_else_quotes_the_git_line(self):
        message = self.assertSays(git_error("hint: something\nfatal: the remote end hung up unexpectedly"))
        self.assertIn("Git said “fatal: the remote end hung up unexpectedly”", message)

    def test_token_never_appears(self):
        repo = SimpleNamespace(name="netops-workflows", branch="main", secrets_group=None)
        exc = git_error(f"fatal: Authentication failed for 'https://bot:{TOKEN_VALUE}@git.example.net/x.git/'")
        with patch("nautobot_workflow_engine.utilities.git_publish.known_secrets", return_value=[TOKEN_VALUE]):
            message = readable_failure(exc, repo)
        self.assertNotIn(TOKEN_VALUE, message)


class PublishBlockerTestCase(TestCase):
    """What a repository needs before Nautobot can push to it."""

    def make(self, *, contents=(WORKFLOWS_CONTENT, WORKFLOW_PUBLISH_CONTENT), secret_types=("token",), url=None):
        group = None
        if secret_types is not None:
            group = SecretsGroup.objects.create(name=f"grp-{uuid.uuid4().hex[:8]}")
            for kind in secret_types:
                secret = Secret.objects.create(
                    name=f"s-{uuid.uuid4().hex[:8]}", provider="environment-variable", parameters={"variable": "X"}
                )
                SecretsGroupAssociation.objects.create(
                    secrets_group=group,
                    secret=secret,
                    access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
                    secret_type=kind,
                )
        return GitRepository.objects.create(
            name=f"repo-{uuid.uuid4().hex[:6]}",
            remote_url=url or f"https://git.example.net/{uuid.uuid4().hex[:6]}.git",
            branch="main",
            secrets_group=group,
            provided_contents=list(contents),
        )

    def test_token_only_group_can_publish(self):
        repo = self.make(secret_types=(SecretsGroupSecretTypeChoices.TYPE_TOKEN,))
        self.assertEqual(publish_blocker(repo), "")
        self.assertTrue(repo_can_publish(repo))

    def test_token_and_username_group_can_publish(self):
        repo = self.make(
            secret_types=(SecretsGroupSecretTypeChoices.TYPE_TOKEN, SecretsGroupSecretTypeChoices.TYPE_USERNAME)
        )
        self.assertEqual(publish_blocker(repo), "")

    def test_publishing_not_ticked(self):
        repo = self.make(contents=(WORKFLOWS_CONTENT,))
        message = publish_blocker(repo)
        self.assertIn("isn't set up for publishing", message)
        self.assertIn("Workflow publishing (Nautobot → Git)", message)
        self.assertIn(repo.name, message)
        self.assertFalse(repo_can_publish(repo))

    def test_no_secrets_group(self):
        message = publish_blocker(self.make(secret_types=None))
        self.assertIn("the repository has no token", message)

    def test_group_without_token(self):
        repo = self.make(secret_types=(SecretsGroupSecretTypeChoices.TYPE_USERNAME,))
        message = publish_blocker(repo)
        self.assertIn(f"its secrets group “{repo.secrets_group.name}” has no token", message)
        self.assertIn("HTTP(S)", message)

    def test_ssh_address(self):
        message = publish_blocker(self.make(url="git@git.example.net:netops/workflows.git"))
        self.assertIn("https://", message)

    def test_none_has_no_blocker(self):
        self.assertEqual(publish_blocker(None), "")
        self.assertFalse(repo_can_publish(None))


class PublishErrorFlowTestCase(BareRepoMixin, TestCase):
    """The reason reaches the workflow row, the API, and the log, against a real local remote."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_protected_branch_on_a_real_remote(self):
        hook = self.bare / "hooks" / "pre-receive"
        hook.write_text(
            '#!/bin/sh\necho "error: GH006: Protected branch update failed for refs/heads/main." >&2\nexit 1\n'
        )
        hook.chmod(0o755)
        with self.assertLogs("nautobot_workflow_engine.utilities.git_publish", level="ERROR") as logs:
            saved, _revision = self.save_and_push(self.definition(key="guarded"))
        self.assertEqual(saved.git_status, GIT_STATUS_NOT_SYNCED)
        self.assertIn(f"Couldn't push to {self.repo.name}: the branch “main” is protected", saved.git_error)
        full = "\n".join(logs.output)
        self.assertIn("pre-receive hook declined", full)
        hook.unlink()
        status = self.client.post(
            reverse("plugins-api:nautobot_workflow_engine-api:workflow-git-push", kwargs={"key": saved.key})
        )
        self.assertEqual(status.data["git_status"], GIT_STATUS_IN_SYNC, status.data)

    def test_plain_message_is_not_redacted(self):
        message = "fatal: Authentication failed for 'https://example.invalid/wf.git'"
        with patch.object(WorkflowGitRepo, "push_once", side_effect=git_error(message)):
            saved, _revision = self.save_and_push(self.definition(key="auth-flow"))
        self.assertIn("the token in its secrets group is valid", saved.git_error)
        self.assertIn("add a Username secret to the same group", saved.git_error)
        self.assertNotIn("(redacted)", saved.git_error)

    def test_publish_api_explains_an_unready_repository(self):
        saved, revision = self.save_and_push(self.definition(key="unready"))
        self.repo.provided_contents = [WORKFLOWS_CONTENT]
        self.repo.save()
        url = reverse("plugins-api:nautobot_workflow_engine-api:workflow-publish", kwargs={"key": saved.key})
        body = {
            "definition": dict(revision.definition, name="Unready again"),
            "base_revision": revision.number,
            "repository": str(self.repo.pk),
        }
        response = self.client.post(url, data=json.dumps(body), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("isn't set up for publishing", response.data["detail"])
        self.assertEqual(response.data["errors"][0]["section"], "details")

    def test_retry_on_an_unready_repository_says_why(self):
        saved, _revision = self.save_and_push(self.definition(key="no-token"))
        self.repo.secrets_group = None
        self.repo.save()
        url = reverse("plugins-api:nautobot_workflow_engine-api:workflow-git-push", kwargs={"key": saved.key})
        response = self.client.post(url)
        self.assertEqual(response.data["git_status"], GIT_STATUS_DISABLED_REPO)
        self.assertIn("the repository has no token", response.data["git_error"])
