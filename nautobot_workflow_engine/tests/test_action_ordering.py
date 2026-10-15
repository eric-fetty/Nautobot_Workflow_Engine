"""Focused regression tests for workflow action ordering."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nautobot_workflow_engine import datasources, executors, models


class WorkflowActionModelOrderingTestCase(unittest.TestCase):
    """Verify the action model exposes the restored order field."""

    def test_workflow_action_has_order_field_and_ordering(self):
        order_field = models.WorkflowAction._meta.get_field("order")

        self.assertEqual(order_field.default, 0)
        self.assertEqual(models.WorkflowAction._meta.ordering, ["phase", "order", "id"])


class ExecutePhaseActionsOrderingTestCase(unittest.TestCase):
    """Verify phase execution uses persisted action order first."""

    def test_execute_phase_actions_orders_by_order_then_id(self):
        class FakeQuerySet(list):
            def __init__(self):
                super().__init__()
                self.order_by_args = None

            def order_by(self, *args):
                self.order_by_args = args
                return self

            def count(self):
                return len(self)

        query_set = FakeQuerySet()
        actions_manager = SimpleNamespace(filter=MagicMock(return_value=query_set))
        workflow_run = SimpleNamespace(
            workflow=SimpleNamespace(source_repo=None, actions=actions_manager),
            log="",
            save=MagicMock(),
        )
        fake_env = SimpleNamespace(from_string=lambda _value: SimpleNamespace(render=lambda _ctx: "true"))

        with patch.object(executors, "get_jinja_environment", return_value=fake_env):
            summary = executors._execute_phase_actions(workflow_run, {"results": {}, "last": {}}, phase="main")

        self.assertEqual(query_set.order_by_args, ("order", "id"))
        self.assertEqual(summary, {"any_failed": False, "results": {}})


class SyncWorkflowActionOrderingTestCase(unittest.TestCase):
    """Verify repo sync persists per-phase action ordering."""

    def test_sync_workflows_from_repo_assigns_action_order_per_phase(self):
        created_actions = []
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
        action_manager = SimpleNamespace(
            create=lambda **kwargs: created_actions.append(kwargs), filter=lambda **_kwargs: action_filter
        )
        helper_manager = SimpleNamespace(filter=MagicMock(return_value=helper_filter))

        data = {
            "key": "sample",
            "name": "Sample",
            "pre_actions": [{"type": "awx", "name": "pre-check", "template_id": 1}],
            "actions": [
                {"type": "nautobot", "name": "first", "script": "scripts/one.py"},
                {"type": "nautobot", "name": "second", "script": "scripts/two.py"},
            ],
            "post_actions": [{"type": "awx", "name": "cleanup", "template_id": 2}],
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
        ):
            datasources.sync_workflows_from_repo(repository_record, job_result, delete=False)

        self.assertEqual([action["phase"] for action in created_actions], ["pre", "main", "main", "post"])
        self.assertEqual([action["order"] for action in created_actions], [1, 1, 2, 1])
