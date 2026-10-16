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
            pk="wf-1",
            name="Orphan",
            key="orphan",
            enabled=True,
            source_repo_id="stale-repo",
            actions=SimpleNamespace(count=MagicMock(return_value=1)),
            inputs=SimpleNamespace(count=MagicMock(return_value=0)),
            save=MagicMock(),
        )
        # strays query + orphan queryset both start from filter(enabled=True).exclude(...)
        stray_qs = MagicMock()
        stray_qs.exclude.return_value = MagicMock(only=MagicMock(return_value=[orphan]))
        orphan_qs = MagicMock()
        orphan_qs.exclude.return_value = [orphan]

        def filter_side_effect(**kwargs):
            self.assertEqual(kwargs, {"enabled": True})
            # First call is strays diagnostic; second is orphan queryset
            if filter_side_effect.calls == 0:
                filter_side_effect.calls += 1
                return stray_qs
            return orphan_qs

        filter_side_effect.calls = 0
        workflow_manager = SimpleNamespace(filter=MagicMock(side_effect=filter_side_effect))
        job_result = SimpleNamespace(log=MagicMock())
        repository_record = SimpleNamespace(id=1, pk=1, remote_url="https://example.com/workflows.git")
        reconcile = MagicMock()
        run_qs = SimpleNamespace(count=MagicMock(return_value=2))

        with patch.object(datasources, "Workflow", SimpleNamespace(objects=workflow_manager)), patch.object(
            datasources, "WorkflowRun", SimpleNamespace(objects=SimpleNamespace(filter=MagicMock(return_value=run_qs)))
        ), patch.object(datasources, "_other_workflow_repo_ids_to_protect", return_value=[]), patch.object(
            datasources, "reconcile_approval_definition", reconcile
        ):
            disabled = datasources._disable_workflows_missing_from_repo(
                repository_record, {"still-present"}, job_result
            )

        self.assertEqual(disabled, 1)
        self.assertFalse(orphan.enabled)
        orphan.save.assert_called_once_with(update_fields=["enabled"])
        reconcile.assert_called_once_with(orphan, job_result=job_result)
        stray_qs.exclude.assert_called_once_with(key__in={"still-present"})
        orphan_qs.exclude.assert_called_once_with(key__in={"still-present"})

    def test_orphan_queryset_disables_stale_source_repo_links(self):
        """Workflows linked to a non-protected (stale/duplicate) repo are orphans."""
        repository_record = SimpleNamespace(pk=42, remote_url="https://example.com/demo.git")
        enabled_qs = MagicMock()
        after_exclude_keys = MagicMock()
        enabled_qs.exclude.return_value = after_exclude_keys
        workflow_manager = SimpleNamespace(filter=MagicMock(return_value=enabled_qs))

        with patch.object(datasources, "Workflow", SimpleNamespace(objects=workflow_manager)), patch.object(
            datasources, "_other_workflow_repo_ids_to_protect", return_value=[]
        ):
            result = datasources._orphan_workflow_queryset(repository_record, {"echo-hostname"})

        workflow_manager.filter.assert_called_once_with(enabled=True)
        enabled_qs.exclude.assert_called_once_with(key__in={"echo-hostname"})
        after_exclude_keys.exclude.assert_not_called()
        self.assertIs(result, after_exclude_keys)

    def test_orphan_queryset_protects_other_workflow_repos(self):
        """Do not disable workflows owned by a different active workflow-providing repo."""
        repository_record = SimpleNamespace(pk=42, remote_url="https://example.com/demo.git")
        enabled_qs = MagicMock()
        after_exclude_keys = MagicMock()
        enabled_qs.exclude.return_value = after_exclude_keys
        workflow_manager = SimpleNamespace(filter=MagicMock(return_value=enabled_qs))

        with patch.object(datasources, "Workflow", SimpleNamespace(objects=workflow_manager)), patch.object(
            datasources, "_other_workflow_repo_ids_to_protect", return_value=["other-repo-pk"]
        ):
            datasources._orphan_workflow_queryset(repository_record, {"echo-hostname"})

        after_exclude_keys.exclude.assert_called_once_with(source_repo_id__in=["other-repo-pk"])

    def test_same_remote_url_repo_is_not_protected(self):
        current = SimpleNamespace(pk=1, remote_url="https://github.com/eric-fetty/Nautobot-Demo-Workflows.git")
        duplicate = SimpleNamespace(
            pk=2,
            remote_url="https://github.com/eric-fetty/Nautobot-Demo-Workflows.git",
            provided_contents=[datasources.WORKFLOW_CONTENT_IDENTIFIER],
        )
        other = SimpleNamespace(
            pk=3,
            remote_url="https://example.com/other.git",
            provided_contents=[datasources.WORKFLOW_CONTENT_IDENTIFIER],
        )
        qs = MagicMock()
        qs.exclude.return_value = qs
        qs.only.return_value = [duplicate, other]

        with patch.object(datasources, "GitRepository", SimpleNamespace(objects=qs)):
            protected = datasources._other_workflow_repo_ids_to_protect(current)

        self.assertEqual(protected, [3])


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
