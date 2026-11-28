"""Hardening from the CodeQL triage: contained paths, a linear model-label check, and server-built edit URLs."""

import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from nautobot_workflow_engine.models import GIT_STATUS_NOT_SYNCED, Workflow
from nautobot_workflow_engine.script_files import parameters_spec
from nautobot_workflow_engine.utilities.safe_paths import UnsafePathError, contained_path


class ContainedPathTestCase(SimpleTestCase):
    """Repository-relative paths can't leave the repository."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_relative_path_inside_the_repository(self):
        path = contained_path(self.root, "workflows/reboot.yaml")
        self.assertEqual(path, Path(os.path.realpath(self.root)) / "workflows" / "reboot.yaml")

    def test_inner_dot_dot_that_stays_inside_is_allowed(self):
        path = contained_path(self.root, "workflows/../scripts/a.py")
        self.assertEqual(path, Path(os.path.realpath(self.root)) / "scripts" / "a.py")

    def test_escapes_are_refused(self):
        for relative in ("../outside.yaml", "workflows/../../outside.yaml", "/etc/passwd", "", ".", "a\x00b"):
            with self.subTest(relative=relative), self.assertRaises(UnsafePathError):
                contained_path(self.root, relative)

    def test_sibling_with_shared_prefix_is_refused(self):
        (Path(self._tmp.name) / "repo-other").mkdir()
        with self.assertRaises(UnsafePathError):
            contained_path(self.root, "../repo-other/x.yaml")

    def test_symlink_out_of_the_repository_is_refused(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(UnsafePathError):
            contained_path(self.root, "link/secret.yaml")


class CurrentFileBytesTestCase(SimpleTestCase):
    """The include scan never reads a file outside the workflow's repository."""

    def test_traversal_returns_nothing(self):
        from nautobot_workflow_engine.services import _current_file_bytes

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "workflows").mkdir(parents=True)
            (repo / "workflows" / "ok.yaml").write_bytes(b"name: ok\n")
            Path(tmp, "secret.txt").write_bytes(b"secret")
            source = SimpleNamespace(filesystem_path=str(repo))
            inside = SimpleNamespace(source_repo_id=1, source_repo=source, git_path="workflows/ok.yaml")
            outside = SimpleNamespace(source_repo_id=1, source_repo=source, git_path="../secret.txt")
            self.assertEqual(_current_file_bytes(inside), b"name: ok\n")
            self.assertEqual(_current_file_bytes(outside), b"")


class RefusePathTestCase(SimpleTestCase):
    """A stored Git path outside the repository leaves the workflow unpublished with a plain error."""

    def test_marks_the_workflow_not_synced(self):
        from nautobot_workflow_engine.utilities.git_publish import UNSAFE_PATH, _refuse_path

        workflow = SimpleNamespace(git_status="", git_error="", save=MagicMock())
        log = MagicMock()
        _refuse_path(SimpleNamespace(name="labs"), workflow, "../escape.yaml", log)
        self.assertEqual(workflow.git_status, GIT_STATUS_NOT_SYNCED)
        self.assertIn(UNSAFE_PATH, workflow.git_error)
        self.assertIn("../escape.yaml", workflow.git_error)
        workflow.save.assert_called_once()
        log.error.assert_called_once()


class ModelLabelTestCase(SimpleTestCase):
    """Object parameters keep ``app_label.model`` and the check stays fast on hostile input."""

    def _model(self, value):
        spec = parameters_spec([{"name": "device", "type": "object", "model": value}])
        return spec["device"].get("model")

    def test_valid_and_invalid_labels(self):
        self.assertEqual(self._model("dcim.device"), "dcim.device")
        for bad in ("dcim", "dcim.", ".device", "dcim.device.extra", "dcim device", 5, None):
            with self.subTest(bad=bad):
                self.assertIsNone(self._model(bad))

    def test_long_input_is_fast(self):
        started = time.monotonic()
        self.assertIsNone(self._model("0" * 50_000))
        self.assertIsNone(self._model("0." + "0" * 50_000 + "!"))
        self.assertLess(time.monotonic() - started, 1.0)


class EditUrlTestCase(TestCase):
    """Create and duplicate answers carry the builder link, so the browser never builds it from page text."""

    def test_summary_has_the_builder_url(self):
        from nautobot_workflow_engine.api.views import _summary

        workflow = Workflow.objects.create(
            key="edit-link",
            name="Edit link",
            raw_definition={"key": "edit-link", "name": "Edit link"},
        )
        expected = reverse("plugins:nautobot_workflow_engine:workflow_builder_edit", kwargs={"key": "edit-link"})
        self.assertEqual(_summary(workflow)["edit_url"], expected)
