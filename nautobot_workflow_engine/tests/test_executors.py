"""Unit tests for executor result normalization."""

# pylint: disable=protected-access,cell-var-from-loop

import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nautobot_workflow_engine import executors


class ExecutorNormalizationTestCase(unittest.TestCase):
    """Validate executor result payload normalization for JSONField persistence."""

    def test_run_action_with_context_serializes_uuid_return_values(self):
        action = SimpleNamespace(action_type="nautobot", with_vars=None)
        workflow_run = SimpleNamespace(workflow=SimpleNamespace(source_repo=None))
        expected_uuid = uuid.uuid4()

        with patch.object(executors, "_run_nautobot_script", return_value={"address_id": expected_uuid}):
            result = executors._run_action_with_context(action, ctx={}, workflow_run=workflow_run)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["return_value"]["address_id"], str(expected_uuid))

    def test_make_json_safe_falls_back_to_string_for_unknown_objects(self):
        class _OpaqueResult:
            pass

        normalized = executors._make_json_safe({"result": _OpaqueResult()})

        self.assertIsInstance(normalized["result"], str)


class NautobotScriptExecutionTestCase(unittest.TestCase):
    """Validate repo-backed script execution behavior."""

    def test_run_nautobot_script_ignores_unrelated_broken_repo_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pkg_root = Path(temp_dir)
            (pkg_root / "scripts").mkdir()
            (pkg_root / "scripts" / "lib").mkdir()
            (pkg_root / "scripts" / "__init__.py").write_text("", encoding="utf-8")
            (pkg_root / "scripts" / "lib" / "__init__.py").write_text("", encoding="utf-8")
            (pkg_root / "scripts" / "lib" / "helper.py").write_text(
                "def format_value(value):\n    return f'formatted:{value}'\n",
                encoding="utf-8",
            )
            (pkg_root / "scripts" / "target.py").write_text(
                "from scripts.lib.helper import format_value\n\n"
                "def run(params):\n    return {'result': format_value(params['value'])}\n",
                encoding="utf-8",
            )
            (pkg_root / "scripts" / "sdd-mcp.py").write_text("import mcp\n", encoding="utf-8")

            action = SimpleNamespace(script_path="scripts/target.py")

            class _Run:
                def __init__(self):
                    self.log = ""
                    self.user_log = ""

                def save(self, update_fields=None):
                    return None

            run = _Run()
            repo = SimpleNamespace(id=123)

            with patch.object(executors, "_materialize_repo_package", return_value=str(pkg_root)), patch.object(
                executors, "_get_internal_manager"
            ) as mock_get_manager:
                mock_get_manager.return_value.get.return_value = object()
                result = executors._run_nautobot_script(action, {"value": "ok"}, run, repo)

        self.assertEqual(result, {"result": "formatted:ok"})
        self.assertNotIn("[preload] Warning", run.log)


class ActionOrderingTestCase(unittest.TestCase):
    """Validate phase action execution preserves persisted order."""

    def test_execute_phase_actions_uses_action_order_field(self):
        executed_actions = []

        class _FakeQuerySet(list):
            def order_by(self, *fields):
                items = list(self)
                for field in reversed(fields):
                    reverse = field.startswith("-")
                    attr = field[1:] if reverse else field
                    items.sort(key=lambda item: getattr(item, attr), reverse=reverse)
                return _FakeQuerySet(items)

            def count(self):
                return len(self)

        class _FakeActionManager:
            def __init__(self, actions):
                self.actions = actions

            def filter(self, **kwargs):
                phase = kwargs.get("phase")
                return _FakeQuerySet([action for action in self.actions if action.phase == phase])

        class _Run:
            def __init__(self, actions):
                self.workflow = SimpleNamespace(source_repo=None, actions=_FakeActionManager(actions))
                self.log = ""

            def save(self, update_fields=None):
                return None

        first = SimpleNamespace(name="run_f5_tcpdump", phase="main", order=0, when="", action_type="nautobot")
        second = SimpleNamespace(name="notify_submitter", phase="main", order=1, when="", action_type="nautobot")
        workflow_run = _Run([second, first])
        ctx = {"results": {}}

        with patch.object(executors, "get_jinja_environment") as mock_get_env, patch.object(
            executors, "_run_action_with_context"
        ) as mock_run_action:
            mock_get_env.return_value.from_string.return_value.render.return_value = "true"

            def _record_action(action, _ctx, _workflow_run):
                executed_actions.append(action.name)
                return {"status": "success", "return_value": action.name}

            mock_run_action.side_effect = _record_action

            summary = executors._execute_phase_actions(workflow_run, ctx, phase="main", fail_fast=False)

        self.assertFalse(summary["any_failed"])
        self.assertEqual(executed_actions, ["run_f5_tcpdump", "notify_submitter"])
        self.assertEqual(ctx["results"]["run_f5_tcpdump"]["status"], "success")
        self.assertEqual(ctx["results"]["notify_submitter"]["status"], "success")
