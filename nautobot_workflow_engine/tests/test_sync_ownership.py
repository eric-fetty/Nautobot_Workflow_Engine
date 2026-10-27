"""Git sync must not overwrite or disable Nautobot-managed workflows."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from nautobot.extras.models import GitRepository

from nautobot_workflow_engine.datasources import sync_workflows_from_repo
from nautobot_workflow_engine.models import MANAGED_BY_GIT, MANAGED_BY_NAUTOBOT, Workflow


class SyncOwnershipTestCase(TestCase):
    """Ownership checks around sync_workflows_from_repo."""

    def setUp(self):
        self.repo = GitRepository.objects.create(name="Repo R", remote_url="https://example.com/r.git")
        self.other = GitRepository.objects.create(name="Repo Q", remote_url="https://example.com/q.git")
        self.logs = []
        self.job_result = SimpleNamespace(log=lambda **kwargs: self.logs.append(kwargs.get("message", "")))

    def _sync(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            workflow_dir = Path(tmp) / "workflows"
            workflow_dir.mkdir()
            for name, text in files.items():
                (workflow_dir / name).write_text(text, encoding="utf-8")
            with patch.object(GitRepository, "filesystem_path", property(lambda _self: tmp)):
                sync_workflows_from_repo(self.repo, self.job_result, delete=False)

    def test_sync_leaves_unpublished_nautobot_workflow_unchanged(self):
        workflow = Workflow.objects.create(
            key="ui-flow",
            name="From the builder",
            enabled=True,
            managed_by=MANAGED_BY_NAUTOBOT,
            source_repo=self.repo,
            raw_definition={"key": "ui-flow", "name": "From the builder", "enabled": True},
        )
        self._sync({"ui-flow.yaml": "key: ui-flow\nname: From Git\nenabled: true\n"})
        workflow.refresh_from_db()
        self.assertEqual(workflow.name, "From the builder")
        self.assertTrue(workflow.enabled)
        self.assertEqual(workflow.managed_by, MANAGED_BY_NAUTOBOT)
        self.assertFalse(any("E-NWE-101" in message for message in self.logs))

    def test_sync_applies_a_newer_git_file_to_an_in_sync_workflow(self):
        workflow = Workflow.objects.create(
            key="ui-flow",
            name="From the builder",
            enabled=True,
            managed_by=MANAGED_BY_NAUTOBOT,
            source_repo=self.repo,
            git_status="in_sync",
            git_content_hash="0" * 64,
            raw_definition={"key": "ui-flow", "name": "From the builder", "enabled": True},
        )
        self._sync({"ui-flow.yaml": "key: ui-flow\nname: From Git\nenabled: true\n"})
        workflow.refresh_from_db()
        self.assertEqual(workflow.name, "From Git")
        self.assertEqual(workflow.managed_by, MANAGED_BY_NAUTOBOT)
        self.assertEqual(workflow.git_status, "in_sync")
        self.assertFalse(any("E-NWE-101" in message for message in self.logs))

    def test_missing_file_does_not_disable_nautobot_managed_workflow(self):
        workflow = Workflow.objects.create(
            key="ui-flow",
            name="From the builder",
            enabled=True,
            managed_by=MANAGED_BY_NAUTOBOT,
            source_repo=self.repo,
            raw_definition={"key": "ui-flow", "name": "From the builder", "enabled": True},
        )
        self._sync({"other.yaml": "key: other-flow\nname: Other\nenabled: true\n"})
        workflow.refresh_from_db()
        self.assertTrue(workflow.enabled)
        self.assertEqual(workflow.raw_definition["name"], "From the builder")

    def test_sync_does_not_take_another_repos_key(self):
        workflow = Workflow.objects.create(
            key="shared",
            name="Owned by Q",
            enabled=True,
            managed_by=MANAGED_BY_GIT,
            source_repo=self.other,
            raw_definition={"key": "shared", "name": "Owned by Q", "enabled": True},
        )
        self._sync({"shared.yaml": "key: shared\nname: Taken by R\nenabled: true\n"})
        workflow.refresh_from_db()
        self.assertEqual(workflow.name, "Owned by Q")
        self.assertEqual(workflow.source_repo_id, self.other.id)
        self.assertTrue(any("E-NWE-101" in message for message in self.logs))
