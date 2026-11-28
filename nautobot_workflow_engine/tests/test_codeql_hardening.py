"""Hardening from the CodeQL triage: contained paths, generic option errors, and a linear model-label check."""

import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from nautobot.extras.models import Tag
from rest_framework.test import APIClient

from nautobot_workflow_engine.models import GIT_STATUS_NOT_SYNCED, Workflow, WorkflowInput
from nautobot_workflow_engine.script_files import parameters_spec
from nautobot_workflow_engine.tests.test_input_options_security import grant
from nautobot_workflow_engine.utilities.safe_paths import UnsafePathError, contained_path
from nautobot_workflow_engine.views import OPTIONS_RENDER_ERROR

User = get_user_model()
BROKEN_TEMPLATE = "{{ inputs."


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


class GenericRenderErrorsTestCase(TestCase):
    """Render failures are logged on the server; the browser gets a fixed message, not exception text."""

    def setUp(self):
        self.user = User.objects.create(username="hardening")
        grant(self.user, ["view", "change"], Workflow)
        grant(self.user, ["view"], Tag)

    def test_input_options_hide_the_exception(self):
        workflow = Workflow.objects.create(
            key="hardening",
            name="Hardening",
            enabled=True,
            raw_definition={"key": "hardening", "name": "Hardening", "enabled": True},
        )
        WorkflowInput.objects.create(
            workflow=workflow,
            key="tag",
            label="Tag",
            input_type="object",
            content_type=ContentType.objects.get_for_model(Tag),
            filter_query={"name": BROKEN_TEMPLATE},
            order=1,
        )
        self.client.force_login(self.user)
        url = reverse("plugins:nautobot_workflow_engine:input_options", kwargs={"key": "hardening", "child": "tag"})
        with self.assertLogs("nautobot_workflow_engine.views", level="ERROR"):
            response = self.client.get(url)
        body = response.json()
        self.assertEqual(body["options"], [])
        self.assertEqual(body["error"], OPTIONS_RENDER_ERROR)

    def test_filter_count_hides_the_exception(self):
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse("plugins-api:nautobot_workflow_engine-api:builder-filter-count")
        with self.assertLogs("nautobot_workflow_engine.api.views", level="WARNING"):
            response = client.post(
                url,
                data=json.dumps({"model": "extras.Tag", "query": {"name": BROKEN_TEMPLATE}}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.data["count"])
        self.assertIn("could not be rendered", response.data["error"])
        self.assertNotIn("unexpected", response.data["error"].lower())


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
