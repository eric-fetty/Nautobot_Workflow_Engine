"""Focused unit tests for repo-backed Jinja filter loading."""


# pylint: disable=protected-access

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nautobot_workflow_engine import executors, models


class _FakeQuerySet(list):
    def order_by(self, *_args, **_kwargs):
        return self


class _FakeManager:
    def __init__(self, items):
        self.items = list(items)

    def filter(self, **_kwargs):
        return _FakeQuerySet(self.items)

    def get(self, **_kwargs):
        if not self.items:
            raise LookupError("No items available")
        return self.items[0]


def _fake_model(items):
    manager = _FakeManager(items)
    return type("FakeModel", (), {"objects": _FakeManager([]), "_base_manager": manager})


def _fake_model_with_managers(base_items, object_items=None):
    return type(
        "FakeModelWithManagers",
        (),
        {
            "objects": _FakeManager(object_items or []),
            "_base_manager": _FakeManager(base_items),
        },
    )


class RepoFilterLoadingTestCase(unittest.TestCase):
    """Validate repo-backed filter loading behavior without database fixtures."""

    def setUp(self):
        self.repo = SimpleNamespace(id=999)
        executors.clear_repo_script_cache(self.repo)

    def tearDown(self):
        executors.clear_repo_script_cache(self.repo)

    def test_repo_filter_can_import_shared_scripts_lib_module(self):
        helper_modules = [
            SimpleNamespace(
                file_path="scripts/lib/text_helpers.py",
                content=(
                    "def normalize_hostname(value):\n" '    return str(value).strip().lower().replace(" ", "-")\n'
                ),
            )
        ]
        filter_modules = [
            SimpleNamespace(
                file_path="filters/string_filters.py",
                content=(
                    "from scripts.lib.text_helpers import normalize_hostname\n\n"
                    "def normalize_for_dns(value):\n"
                    "    return normalize_hostname(value).replace('_', '-')\n\n"
                    "FILTERS = {'normalize_for_dns': normalize_for_dns}\n"
                ),
            )
        ]

        with patch.object(models, "WorkflowHelper", _fake_model(helper_modules)), patch.object(
            models, "WorkflowFilterModule", _fake_model(filter_modules)
        ):
            rendered = executors._render("{{ value | normalize_for_dns }}", {"value": " Core Switch "}, repo=self.repo)

        self.assertEqual(rendered, "core-switch")

    def test_repo_filter_import_with_missing_runtime_dependency_fails_validation(self):
        filter_modules = [
            SimpleNamespace(
                file_path="filters/bad_filters.py",
                content=("import package_that_does_not_exist\n\n" "FILTERS = {'noop': lambda value: value}\n"),
            )
        ]

        with patch.object(models, "WorkflowHelper", _fake_model([])), patch.object(
            models, "WorkflowFilterModule", _fake_model(filter_modules)
        ):
            with self.assertRaisesRegex(RuntimeError, "package_that_does_not_exist"):
                executors.validate_repo_filter_modules(self.repo)

    def test_repo_filter_name_collision_with_builtin_filter_fails_validation(self):
        filter_modules = [
            SimpleNamespace(
                file_path="filters/colliding_filters.py",
                content=("FILTERS = {'ip_address': lambda value: value}\n"),
            )
        ]

        with patch.object(models, "WorkflowHelper", _fake_model([])), patch.object(
            models, "WorkflowFilterModule", _fake_model(filter_modules)
        ):
            with self.assertRaisesRegex(RuntimeError, "collides with a built-in filter"):
                executors.validate_repo_filter_modules(self.repo)

    def test_repo_filter_loading_uses_base_manager_when_objects_is_restricted(self):
        helper_modules = [
            SimpleNamespace(
                file_path="scripts/lib/text_helpers.py",
                content=(
                    "def normalize_hostname(value):\n" '    return str(value).strip().lower().replace(" ", "-")\n'
                ),
            )
        ]
        filter_modules = [
            SimpleNamespace(
                file_path="filters/string_filters.py",
                content=(
                    "from scripts.lib.text_helpers import normalize_hostname\n\n"
                    "def normalize_for_dns(value):\n"
                    "    return normalize_hostname(value).replace('_', '-')\n\n"
                    "FILTERS = {'normalize_for_dns': normalize_for_dns}\n"
                ),
            )
        ]

        with patch.object(
            models, "WorkflowHelper", _fake_model_with_managers(helper_modules, object_items=[])
        ), patch.object(models, "WorkflowFilterModule", _fake_model_with_managers(filter_modules, object_items=[])):
            rendered = executors._render("{{ value | normalize_for_dns }}", {"value": " Core Switch "}, repo=self.repo)

        self.assertEqual(rendered, "core-switch")
