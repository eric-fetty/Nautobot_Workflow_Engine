"""GitHub and GitLab push webhooks."""

import hashlib
import hmac
import json
import os
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import GitRepository, Secret, SecretsGroup, SecretsGroupAssociation
from rest_framework.test import APIClient

from nautobot_workflow_engine.models import WORKFLOWS_CONTENT

HOOK_ENV = "NWE_TEST_WEBHOOK_SECRET"
HOOK_VALUE = "hook-secret-value"


class GitWebhookTestCase(TestCase):
    """Signature checks, branch filtering, and one enqueue per ten seconds."""

    def setUp(self):
        super().setUp()
        os.environ[HOOK_ENV] = HOOK_VALUE
        self.addCleanup(lambda: os.environ.pop(HOOK_ENV, None))
        get_user_model().objects.create(username=f"hook-{uuid.uuid4().hex[:6]}")
        secret = Secret.objects.create(
            name=f"hook-{uuid.uuid4().hex[:8]}",
            provider="environment-variable",
            parameters={"variable": HOOK_ENV},
        )
        group = SecretsGroup.objects.create(name=f"hooks-{uuid.uuid4().hex[:8]}")
        SecretsGroupAssociation.objects.create(
            secrets_group=group,
            secret=secret,
            access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_KEY,
        )
        self.repo = GitRepository.objects.create(
            name=f"Hook {uuid.uuid4().hex[:8]}",
            remote_url=f"https://example.invalid/hook-{uuid.uuid4().hex[:6]}.git",
            branch="main",
            secrets_group=group,
            provided_contents=[WORKFLOWS_CONTENT],
        )
        self.plain = GitRepository.objects.create(
            name=f"Plain {uuid.uuid4().hex[:8]}",
            remote_url=f"https://example.invalid/plain-{uuid.uuid4().hex[:6]}.git",
            branch="main",
        )
        self.client = APIClient()
        self.url = reverse(
            "plugins-api:nautobot_workflow_engine-api:git-webhook",
            kwargs={"repository_id": self.repo.pk},
        )

    def _post(self, body, **headers):
        return self.client.post(self.url, data=body, content_type="application/json", **headers)

    def _github(self, body, secret=HOOK_VALUE, event="push"):
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return self._post(body, HTTP_X_HUB_SIGNATURE_256=f"sha256={digest}", HTTP_X_GITHUB_EVENT=event)

    def test_github_signature_branch_and_dedupe(self):
        body = json.dumps({"ref": "refs/heads/main"}).encode()
        with patch(
            "nautobot.extras.datasources.git.enqueue_pull_git_repository_and_refresh_data",
            return_value=None,
        ) as enqueued:
            valid = self._github(body)
            again = self._github(body)
            other = self._github(json.dumps({"ref": "refs/heads/other"}).encode())
            ping = self._github(body, event="ping")
        self.assertEqual(valid.status_code, 202)
        self.assertTrue(valid.data["enqueued"])
        self.assertEqual(again.status_code, 202)
        self.assertFalse(again.data["enqueued"])
        self.assertEqual(again.data["reason"], "duplicate")
        self.assertEqual(other.status_code, 202)
        self.assertTrue(other.data["ignored"])
        self.assertEqual(ping.status_code, 202)
        self.assertTrue(ping.data["ignored"])
        self.assertEqual(enqueued.call_count, 1)
        bad = self._github(body, secret="wrong-secret")
        self.assertEqual(bad.status_code, 403)

    def test_gitlab_token_and_missing_secret(self):
        body = json.dumps({"object_kind": "push", "ref": "refs/heads/main"}).encode()
        with patch("nautobot.extras.datasources.git.enqueue_pull_git_repository_and_refresh_data", return_value=None):
            accepted = self._post(body, HTTP_X_GITLAB_TOKEN=HOOK_VALUE)
        self.assertEqual(accepted.status_code, 202)
        self.assertTrue(accepted.data["enqueued"])
        rejected = self._post(body, HTTP_X_GITLAB_TOKEN="nope")
        self.assertEqual(rejected.status_code, 403)
        missing = self.client.post(
            reverse(
                "plugins-api:nautobot_workflow_engine-api:git-webhook",
                kwargs={"repository_id": self.plain.pk},
            ),
            data=body,
            content_type="application/json",
            HTTP_X_GITLAB_TOKEN=HOOK_VALUE,
        )
        self.assertEqual(missing.status_code, 404)
        unknown = self.client.post(
            reverse(
                "plugins-api:nautobot_workflow_engine-api:git-webhook",
                kwargs={"repository_id": uuid.uuid4()},
            ),
            data=body,
            content_type="application/json",
        )
        self.assertEqual(unknown.status_code, 404)
