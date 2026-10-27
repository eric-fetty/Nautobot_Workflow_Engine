"""Regression tests for disabling workflows removed from a Git repository."""

# pylint: disable=protected-access

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.test import TestCase
from nautobot.extras.models import GitRepository

from nautobot_workflow_engine import datasources
from nautobot_workflow_engine.models import MANAGED_BY_GIT, MANAGED_BY_NAUTOBOT, WORKFLOWS_CONTENT, Workflow


class OrphanCleanupTestCase(TestCase):
    """Orphan cleanup only ever switches off Git-managed workflows (merged from develop 28d472a)."""

    def setUp(self):
        self.repo = GitRepository.objects.create(
            name="Live", remote_url="https://example.com/flows.git", provided_contents=[WORKFLOWS_CONTENT]
        )
        self.logs = []
        self.job_result = SimpleNamespace(log=lambda *args, **kwargs: self.logs.append(kwargs.get("message", "")))

    def _workflow(self, key, managed_by=MANAGED_BY_GIT, repo="live"):
        return Workflow.objects.create(
            key=key,
            name=key,
            enabled=True,
            managed_by=managed_by,
            source_repo=self.repo if repo == "live" else repo,
            raw_definition={"key": key, "name": key},
        )

    def _sync(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            workflow_dir = Path(tmp) / "workflows"
            workflow_dir.mkdir()
            for name, text in files.items():
                (workflow_dir / name).write_text(text, encoding="utf-8")
            with patch.object(GitRepository, "filesystem_path", property(lambda _self: tmp)):
                datasources.sync_workflows_from_repo(self.repo, self.job_result, delete=False)

    def _enabled(self, workflow):
        workflow.refresh_from_db()
        return workflow.enabled

    def test_removed_git_workflow_is_switched_off(self):
        gone = self._workflow("gone")
        self._sync({})
        self.assertFalse(self._enabled(gone))
        self.assertTrue(any("Disabling workflow 'gone'" in message for message in self.logs))
        self.assertTrue(any("Checking for removed workflows: 1" in message for message in self.logs))

    def test_ui_built_and_published_workflows_are_never_switched_off(self):
        db_only = self._workflow("built-in-ui", MANAGED_BY_NAUTOBOT, repo=None)
        published = self._workflow("published-from-ui", MANAGED_BY_NAUTOBOT)
        self._sync({})
        self.assertTrue(self._enabled(db_only))
        self.assertTrue(self._enabled(published))

    def test_stale_links_are_cleaned_up(self):
        duplicate = GitRepository.objects.create(
            name="Live (old copy)", remote_url=self.repo.remote_url, provided_contents=[WORKFLOWS_CONTENT]
        )
        retired = GitRepository.objects.create(name="Retired", remote_url="https://example.com/old.git")
        from_duplicate = self._workflow("from-duplicate", repo=duplicate)
        from_retired = self._workflow("from-retired", repo=retired)
        unlinked = self._workflow("unlinked", repo=None)
        self._sync({})
        self.assertFalse(self._enabled(from_duplicate))
        self.assertFalse(self._enabled(from_retired))
        self.assertFalse(self._enabled(unlinked))

    def test_another_live_repository_keeps_its_workflows(self):
        other = GitRepository.objects.create(
            name="Other", remote_url="https://example.com/other.git", provided_contents=[WORKFLOWS_CONTENT]
        )
        theirs = self._workflow("theirs", repo=other)
        self._sync({})
        self.assertTrue(self._enabled(theirs))

    def test_a_broken_file_does_not_switch_its_workflow_off(self):
        kept = self._workflow("kept")
        self._sync({"kept.yaml": "key: kept\nname: Kept\ninputs: not-a-list\n"})
        self.assertTrue(self._enabled(kept))
        self.assertTrue(any("E-NWE-102" in message for message in self.logs))


class SyncDisablesRemovedWorkflowsTestCase(unittest.TestCase):
    """Verify normal git sync disables workflows no longer present in the repo."""

    def test_sync_disables_orphans_when_delete_is_false(self):
        workflow = SimpleNamespace(
            key="sample", inputs=SimpleNamespace(all=lambda: SimpleNamespace(delete=lambda: None))
        )

        class FakeWorkflowPath:
            def exists(self):
                return True

            def glob(self, _pattern):
                return [SimpleNamespace()]

        class FakeRepoPath:
            def __truediv__(self, _other):
                return FakeWorkflowPath()

        repository_record = SimpleNamespace(filesystem_path="/fake/repo")
        job_result = SimpleNamespace(log=MagicMock())
        action_filter = SimpleNamespace(delete=MagicMock())
        helper_filter = SimpleNamespace(exists=MagicMock(return_value=True))
        workflow_manager = SimpleNamespace(update_or_create=MagicMock(return_value=(workflow, True)))
        input_manager = SimpleNamespace(create=MagicMock())
        action_manager = SimpleNamespace(create=MagicMock(), filter=lambda **_kwargs: action_filter)
        helper_manager = SimpleNamespace(filter=MagicMock(return_value=helper_filter))
        disable_orphans = MagicMock(return_value=1)

        data = {
            "key": "sample",
            "name": "Sample",
            "actions": [{"type": "awx", "name": "main", "template_id": 1}],
        }

        with patch.object(datasources, "Path", return_value=FakeRepoPath()), patch.object(
            datasources, "_sync_repo_python_modules"
        ), patch.object(datasources, "validate_repo_filter_modules"), patch.object(
            datasources, "_process_includes", return_value="ignored"
        ), patch.object(datasources.yaml, "safe_load", return_value=data), patch.object(
            datasources, "Workflow", SimpleNamespace(objects=workflow_manager)
        ), patch.object(datasources, "WorkflowInput", SimpleNamespace(objects=input_manager)), patch.object(
            datasources, "WorkflowAction", SimpleNamespace(objects=action_manager)
        ), patch.object(datasources, "WorkflowHelper", SimpleNamespace(objects=helper_manager)), patch.object(
            datasources, "reconcile_approval_definition"
        ), patch.object(datasources, "_disable_workflows_missing_from_repo", disable_orphans), patch.object(
            datasources, "clear_repo_script_cache"
        ):
            datasources.sync_workflows_from_repo(repository_record, job_result, delete=False)

        disable_orphans.assert_called_once_with(repository_record, {"sample"}, job_result)

    def test_sync_disables_all_when_workflows_directory_missing(self):
        class FakeWorkflowPath:
            def exists(self):
                return False

        class FakeRepoPath:
            def __truediv__(self, _other):
                return FakeWorkflowPath()

        repository_record = SimpleNamespace(filesystem_path="/fake/repo")
        job_result = SimpleNamespace(log=MagicMock())
        disable_orphans = MagicMock(return_value=2)

        with patch.object(datasources, "Path", return_value=FakeRepoPath()), patch.object(
            datasources, "_disable_workflows_missing_from_repo", disable_orphans
        ), patch.object(datasources, "clear_repo_script_cache") as clear_cache:
            datasources.sync_workflows_from_repo(repository_record, job_result, delete=False)

        disable_orphans.assert_called_once_with(repository_record, set(), job_result)
        clear_cache.assert_called_once_with(repository_record)

    def test_delete_disables_all_and_clears_helpers(self):
        repository_record = SimpleNamespace(filesystem_path="/fake/repo")
        job_result = SimpleNamespace(log=MagicMock())
        disable_orphans = MagicMock(return_value=3)
        helper_qs = MagicMock()
        filter_qs = MagicMock()

        with patch.object(datasources, "_disable_workflows_missing_from_repo", disable_orphans), patch.object(
            datasources,
            "WorkflowHelper",
            SimpleNamespace(objects=SimpleNamespace(filter=MagicMock(return_value=helper_qs))),
        ), patch.object(
            datasources,
            "WorkflowFilterModule",
            SimpleNamespace(objects=SimpleNamespace(filter=MagicMock(return_value=filter_qs))),
        ), patch.object(datasources, "clear_repo_script_cache") as clear_cache, patch.object(
            datasources, "Path"
        ) as path_mock:
            datasources.sync_workflows_from_repo(repository_record, job_result, delete=True)

        disable_orphans.assert_called_once_with(repository_record, set(), job_result)
        helper_qs.delete.assert_called_once_with()
        filter_qs.delete.assert_called_once_with()
        clear_cache.assert_called_once_with(repository_record)
        path_mock.assert_not_called()
        self.assertEqual(disable_orphans.call_args, call(repository_record, set(), job_result))
