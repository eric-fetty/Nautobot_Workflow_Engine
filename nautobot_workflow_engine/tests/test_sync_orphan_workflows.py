"""Regression tests for disabling workflows removed from a Git repository."""

# pylint: disable=protected-access

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from nautobot_workflow_engine import datasources


class DisableWorkflowsMissingFromRepoTestCase(unittest.TestCase):
    """Unit tests for orphan workflow disable helper."""

    def test_disables_enabled_workflows_not_in_synced_keys(self):
        orphan = SimpleNamespace(
            name="Orphan",
            key="orphan",
            enabled=True,
            actions=SimpleNamespace(count=MagicMock(return_value=1)),
            inputs=SimpleNamespace(count=MagicMock(return_value=0)),
            runs=SimpleNamespace(count=MagicMock(return_value=2)),
            save=MagicMock(),
        )
        qs = MagicMock()
        qs.exclude.return_value = [orphan]
        workflow_manager = SimpleNamespace(filter=MagicMock(return_value=qs))
        job_result = SimpleNamespace(log=MagicMock())
        repository_record = SimpleNamespace(id=1)
        reconcile = MagicMock()

        with patch.object(datasources, "Workflow", SimpleNamespace(objects=workflow_manager)), patch.object(
            datasources, "reconcile_approval_definition", reconcile
        ):
            disabled = datasources._disable_workflows_missing_from_repo(
                repository_record, {"still-present"}, job_result
            )

        self.assertEqual(disabled, 1)
        self.assertFalse(orphan.enabled)
        orphan.save.assert_called_once_with(update_fields=["enabled"])
        reconcile.assert_called_once_with(orphan, job_result=job_result)
        workflow_manager.filter.assert_called_once_with(source_repo=repository_record, enabled=True)
        qs.exclude.assert_called_once_with(key__in={"still-present"})


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
            datasources, "WorkflowHelper", SimpleNamespace(objects=SimpleNamespace(filter=MagicMock(return_value=helper_qs)))
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
