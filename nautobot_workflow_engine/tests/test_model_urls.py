"""Regression tests for get_absolute_url on models without dedicated detail views."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from nautobot_workflow_engine import models


class InternalModelAbsoluteUrlTestCase(unittest.TestCase):
    """Change-logging needs get_absolute_url during git sync create/delete."""

    def test_workflow_filter_module_delegates_to_source_repo(self):
        source_repo = SimpleNamespace(get_absolute_url=MagicMock(return_value="/api/extras/git-repositories/1/"))
        module = models.WorkflowFilterModule(file_path="filters/string_filters.py")
        module.source_repo = source_repo

        url = module.get_absolute_url(api=True)

        self.assertEqual(url, "/api/extras/git-repositories/1/")
        source_repo.get_absolute_url.assert_called_once_with(api=True)

    def test_workflow_helper_delegates_to_source_repo(self):
        source_repo = SimpleNamespace(get_absolute_url=MagicMock(return_value="/extras/git-repositories/1/"))
        helper = models.WorkflowHelper(file_path="scripts/echo.py")
        helper.source_repo = source_repo

        url = helper.get_absolute_url()

        self.assertEqual(url, "/extras/git-repositories/1/")
        source_repo.get_absolute_url.assert_called_once_with()

    def test_workflow_input_delegates_to_workflow(self):
        workflow = SimpleNamespace(get_absolute_url=MagicMock(return_value="/plugins/workflows/sample/launch/"))
        workflow_input = models.WorkflowInput(key="hostname")
        workflow_input.workflow = workflow

        url = workflow_input.get_absolute_url()

        self.assertEqual(url, "/plugins/workflows/sample/launch/")
        workflow.get_absolute_url.assert_called_once_with()

    def test_awx_run_delegates_to_workflow_run(self):
        workflow_run = SimpleNamespace(get_absolute_url=MagicMock(return_value="/plugins/runs/abc/"))
        awx_run = models.AwxRun(template_id=1)
        awx_run.workflow_run = workflow_run

        url = awx_run.get_absolute_url(api=False)

        self.assertEqual(url, "/plugins/runs/abc/")
        workflow_run.get_absolute_url.assert_called_once_with(api=False)
