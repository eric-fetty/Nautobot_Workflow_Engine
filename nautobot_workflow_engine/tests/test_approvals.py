"""Unit tests for optional Nautobot approval workflow integration."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nautobot_workflow_engine import approvals, datasources


class ApprovalHelpersTestCase(unittest.TestCase):
    """Validate naming, constraints, and version gating helpers."""

    def test_approval_definition_name_and_constraints(self):
        workflow = SimpleNamespace(key="interface-ip")

        self.assertEqual(approvals.approval_definition_name(workflow), "Workflow Engine: interface-ip")
        self.assertEqual(
            approvals.approval_constraints(workflow),
            {
                "job_model__job_class_name": "ScheduledWorkflowExecutorJob",
                "kwargs__workflow_key": "interface-ip",
            },
        )

    def test_nautobot_supports_approval_workflows_version_gate(self):
        with patch("importlib.metadata.version", return_value="3.1.9"):
            self.assertFalse(approvals.nautobot_supports_approval_workflows())

        with patch("importlib.metadata.version", return_value="3.2.0"):
            self.assertTrue(approvals.nautobot_supports_approval_workflows())

        with patch("importlib.metadata.version", return_value="3.2.5"):
            self.assertTrue(approvals.nautobot_supports_approval_workflows())


class ReconcileApprovalDefinitionTestCase(unittest.TestCase):
    """Validate create/update/delete of ApprovalWorkflowDefinition shells."""

    def test_reconcile_skips_when_unsupported(self):
        workflow = SimpleNamespace(key="sample", approval_required=True, enabled=True)
        job_result = SimpleNamespace(log=MagicMock())

        with patch.object(approvals, "nautobot_supports_approval_workflows", return_value=False):
            result = approvals.reconcile_approval_definition(workflow, job_result=job_result)

        self.assertIsNone(result)
        job_result.log.assert_called()
        self.assertIn("Skipping approval definition", job_result.log.call_args.kwargs["message"])

    def test_upsert_creates_definition_without_stages(self):
        workflow = SimpleNamespace(key="sample", approval_required=True, enabled=True)
        job_result = SimpleNamespace(log=MagicMock())
        content_type = SimpleNamespace(id=7)
        created = SimpleNamespace(name="Workflow Engine: sample", weight=3)

        mock_awd = MagicMock()
        mock_awd.objects.filter.return_value.first.return_value = None
        mock_awd.objects.filter.return_value.aggregate.return_value = {"weight__max": 2}
        mock_awd.objects.create.return_value = created

        with patch.object(approvals, "nautobot_supports_approval_workflows", return_value=True), patch(
            "nautobot.extras.models.ApprovalWorkflowDefinition", mock_awd
        ), patch("nautobot.extras.models.ScheduledJob", MagicMock()), patch(
            "nautobot_workflow_engine.approvals.ContentType.objects.get_for_model", return_value=content_type
        ):
            result = approvals.reconcile_approval_definition(workflow, job_result=job_result)

        self.assertIs(result, created)
        create_kwargs = mock_awd.objects.create.call_args.kwargs
        self.assertEqual(create_kwargs["name"], "Workflow Engine: sample")
        self.assertEqual(
            create_kwargs["model_constraints"],
            {
                "job_model__job_class_name": "ScheduledWorkflowExecutorJob",
                "kwargs__workflow_key": "sample",
            },
        )
        self.assertEqual(create_kwargs["weight"], 3)
        self.assertIn("Add at least one approval stage", job_result.log.call_args.kwargs["message"])

    def test_upsert_updates_existing_constraints(self):
        workflow = SimpleNamespace(key="sample", approval_required=True, enabled=True)
        existing = MagicMock()
        existing.model_constraints = {}
        existing.model_content_type_id = 7
        existing.save = MagicMock()

        mock_awd = MagicMock()
        mock_awd.objects.filter.return_value.first.return_value = existing

        with patch.object(approvals, "nautobot_supports_approval_workflows", return_value=True), patch(
            "nautobot.extras.models.ApprovalWorkflowDefinition", mock_awd
        ), patch("nautobot.extras.models.ScheduledJob", MagicMock()), patch(
            "nautobot_workflow_engine.approvals.ContentType.objects.get_for_model",
            return_value=SimpleNamespace(id=7),
        ):
            result = approvals.reconcile_approval_definition(workflow)

        self.assertIs(result, existing)
        self.assertEqual(
            existing.model_constraints,
            {
                "job_model__job_class_name": "ScheduledWorkflowExecutorJob",
                "kwargs__workflow_key": "sample",
            },
        )
        existing.save.assert_called_once()

    def test_reconcile_deletes_when_flag_off(self):
        workflow = SimpleNamespace(key="sample", approval_required=False, enabled=True)
        existing = MagicMock()
        existing.delete = MagicMock()

        mock_awd = MagicMock()
        mock_awd.objects.filter.return_value.first.return_value = existing

        with patch.object(approvals, "nautobot_supports_approval_workflows", return_value=True), patch(
            "nautobot.extras.models.ApprovalWorkflowDefinition", mock_awd
        ):
            result = approvals.reconcile_approval_definition(workflow)

        self.assertIsNone(result)
        existing.delete.assert_called_once()

    def test_reconcile_deletes_when_workflow_disabled(self):
        workflow = SimpleNamespace(key="sample", approval_required=True, enabled=False)
        existing = MagicMock()
        existing.delete = MagicMock()

        mock_awd = MagicMock()
        mock_awd.objects.filter.return_value.first.return_value = existing

        with patch.object(approvals, "nautobot_supports_approval_workflows", return_value=True), patch(
            "nautobot.extras.models.ApprovalWorkflowDefinition", mock_awd
        ):
            approvals.reconcile_approval_definition(workflow)

        existing.delete.assert_called_once()

    def test_delete_logs_warning_when_blocked(self):
        workflow = SimpleNamespace(key="sample")
        job_result = SimpleNamespace(log=MagicMock())
        existing = MagicMock()
        existing.delete.side_effect = Exception("pending workflows exist")

        mock_awd = MagicMock()
        mock_awd.objects.filter.return_value.first.return_value = existing

        with patch("nautobot.extras.models.ApprovalWorkflowDefinition", mock_awd):
            deleted = approvals.delete_approval_definition(workflow, job_result=job_result)

        self.assertFalse(deleted)
        self.assertIn("Unable to delete", job_result.log.call_args.kwargs["message"])


class SyncApprovalRequiredTestCase(unittest.TestCase):
    """Validate git sync persists approval_required and reconciles definitions."""

    def test_sync_sets_approval_required_and_reconciles(self):
        created_defaults = {}
        workflow = SimpleNamespace(
            key="sample",
            inputs=SimpleNamespace(all=lambda: SimpleNamespace(delete=lambda: None)),
            approval_required=True,
            enabled=True,
        )

        class FakeWorkflowPath:
            def exists(self):
                return True

            def glob(self, _pattern):
                return [SimpleNamespace()]

        class FakeRepoPath:
            def __truediv__(self, _other):
                return FakeWorkflowPath()

        def update_or_create(**kwargs):
            created_defaults.update(kwargs.get("defaults", {}))
            workflow.key = kwargs["key"]
            workflow.approval_required = created_defaults.get("approval_required", False)
            workflow.enabled = created_defaults.get("enabled", True)
            return workflow, True

        repository_record = SimpleNamespace(filesystem_path="/fake/repo")
        job_result = SimpleNamespace(log=MagicMock())
        action_filter = SimpleNamespace(delete=MagicMock())
        helper_filter = SimpleNamespace(exists=MagicMock(return_value=True))
        workflow_manager = SimpleNamespace(update_or_create=MagicMock(side_effect=update_or_create))
        input_manager = SimpleNamespace(create=MagicMock())
        action_manager = SimpleNamespace(
            create=MagicMock(),
            filter=lambda **_kwargs: action_filter,
        )
        helper_manager = SimpleNamespace(filter=MagicMock(return_value=helper_filter))
        reconcile = MagicMock()

        data = {
            "key": "sample",
            "name": "Sample",
            "approval_required": True,
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
            datasources, "reconcile_approval_definition", reconcile
        ), patch.object(datasources, "_disable_workflows_missing_from_repo"):
            datasources.sync_workflows_from_repo(repository_record, job_result, delete=False)

        self.assertTrue(created_defaults["approval_required"])
        reconcile.assert_called_once_with(workflow, job_result=job_result)

    def test_sync_accepts_legacy_requires_approval_yaml_key(self):
        created_defaults = {}
        workflow = SimpleNamespace(
            key="sample",
            inputs=SimpleNamespace(all=lambda: SimpleNamespace(delete=lambda: None)),
            approval_required=False,
            enabled=True,
        )

        class FakeWorkflowPath:
            def exists(self):
                return True

            def glob(self, _pattern):
                return [SimpleNamespace()]

        class FakeRepoPath:
            def __truediv__(self, _other):
                return FakeWorkflowPath()

        def update_or_create(**kwargs):
            created_defaults.update(kwargs.get("defaults", {}))
            workflow.key = kwargs["key"]
            workflow.approval_required = created_defaults.get("approval_required", False)
            workflow.enabled = created_defaults.get("enabled", True)
            return workflow, True

        repository_record = SimpleNamespace(filesystem_path="/fake/repo")
        job_result = SimpleNamespace(log=MagicMock())
        action_filter = SimpleNamespace(delete=MagicMock())
        helper_filter = SimpleNamespace(exists=MagicMock(return_value=True))
        workflow_manager = SimpleNamespace(update_or_create=MagicMock(side_effect=update_or_create))
        input_manager = SimpleNamespace(create=MagicMock())
        action_manager = SimpleNamespace(
            create=MagicMock(),
            filter=lambda **_kwargs: action_filter,
        )
        helper_manager = SimpleNamespace(filter=MagicMock(return_value=helper_filter))

        data = {
            "key": "sample",
            "name": "Sample",
            "requires_approval": True,
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
            datasources, "reconcile_approval_definition", MagicMock()
        ), patch.object(datasources, "_disable_workflows_missing_from_repo"):
            datasources.sync_workflows_from_repo(repository_record, job_result, delete=False)

        self.assertTrue(created_defaults["approval_required"])
