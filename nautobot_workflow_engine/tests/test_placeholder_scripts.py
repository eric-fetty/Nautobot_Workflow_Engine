"""Placeholder scripts: generated from the step, parsed to check, committed switched off, never run here."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from nautobot_workflow_engine import executors
from nautobot_workflow_engine.models import Workflow, WorkflowHelper
from nautobot_workflow_engine.script_files import (
    SWITCHED_OFF_MESSAGE,
    check_script,
    parameters_spec,
    render_script,
    script_enabled,
    script_path,
    script_slug,
)
from nautobot_workflow_engine.script_params import script_parameters
from nautobot_workflow_engine.tests.git_support import BareRepoMixin, grant

User = get_user_model()
PLUGINS = {"nautobot_workflow_engine": {"scripts_dir": "scripts"}}


def placeholder(body=None):
    spec = parameters_spec(
        [
            {"name": "device", "type": "object", "model": "dcim.Device", "help": "Device"},
            {"name": "attempts", "type": "integer", "required": False},
        ]
    )
    return render_script(path="scripts/check_it.py", title="Check it", spec=spec, body=body)


@override_settings(PLUGINS_CONFIG=PLUGINS)
class ScriptFileTestCase(SimpleTestCase):
    """The generated file and its checks."""

    def test_placeholder_matches_the_scripts_format(self):
        text = placeholder()
        self.assertEqual(check_script(text), [])
        self.assertIn("ENABLED = False", text)
        self.assertIn('"device": {"type": "object", "required": True, "model": "dcim.Device", "help": "Device"}', text)
        self.assertIn("def run(params):", text)
        meta = script_parameters(text)
        self.assertTrue(meta["declared"])
        self.assertEqual([row["name"] for row in meta["params"]], ["device", "attempts"])
        self.assertEqual(meta["params"][0]["model"], "dcim.Device")
        self.assertFalse(meta["params"][1]["required"])
        self.assertFalse(script_enabled(text))

    def test_body_is_placed_inside_run(self):
        text = placeholder('from nautobot.dcim.models import Device\nreturn {"n": Device.objects.count()}')
        self.assertEqual(check_script(text), [])
        self.assertIn("    from nautobot.dcim.models import Device\n", text)

    def test_problems_are_plain_and_point_at_a_line(self):
        broken = check_script(placeholder('return {"oops": }'))
        self.assertEqual(len(broken), 1)
        self.assertTrue(broken[0]["message"].startswith(f"Line {broken[0]['line']}:"), broken)
        top_import = check_script("import os\n\ndef run(params):\n    return {}\n")
        self.assertIn("Put imports and other code inside run()", top_import[0]["message"])
        switched_on = check_script("ENABLED = True\n\ndef run(params):\n    return {}\n")
        self.assertIn("start switched off", switched_on[0]["message"])
        decorated = check_script("@staticmethod\ndef run(params):\n    return {}\n")
        self.assertIn("def run(params):", decorated[0]["message"])
        self.assertIn("needs a run(params)", check_script('"""Only a docstring."""\n')[0]["message"])

    def test_checking_never_runs_the_code(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "ran"
            text = f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(3)\n\ndef run(params):\n    return {{}}\n"
            problems = check_script(text)
            self.assertTrue(problems)
            self.assertFalse(marker.exists())
            self.assertTrue(script_enabled(text))

    def test_names_and_parameters_are_sanitized(self):
        self.assertEqual(script_slug("Check reachability!"), "check_reachability")
        self.assertEqual(script_slug("1st try"), "script_1st_try")
        self.assertEqual(script_slug("class"), "class_script")
        self.assertEqual(script_slug("../../etc/passwd"), "etc_passwd")
        self.assertEqual(script_path("Post change note"), "scripts/post_change_note.py")
        spec = parameters_spec(
            [
                {"name": "ok_name", "type": "weird"},
                {"name": "class"},
                {"name": "a-b"},
                {"name": "x", "type": "object", "model": "dcim.Device; import os"},
            ]
        )
        self.assertEqual(list(spec), ["ok_name", "x"])
        self.assertEqual(spec["ok_name"]["type"], "string")
        self.assertNotIn("model", spec["x"])

    def test_older_scripts_without_the_flag_stay_enabled(self):
        self.assertTrue(script_enabled("def run(params):\n    return {}\n"))


class SwitchedOffScriptTestCase(SimpleTestCase):
    """The runner refuses a switched-off script before importing it."""

    def test_switched_off_script_is_not_imported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            marker = root / "imported"
            (root / "scripts" / "off.py").write_text(
                f"ENABLED = False\nopen({str(marker)!r}, 'w').write('x')\n\ndef run(params):\n    return {{}}\n"
            )
            row = SimpleNamespace(content=(root / "scripts" / "off.py").read_text())
            run = SimpleNamespace(log="", user_log="", save=lambda **kwargs: None)
            with (
                patch.object(executors, "_materialize_repo_package", return_value=str(root)),
                patch.object(executors, "_get_internal_manager") as manager,
            ):
                manager.return_value.get.return_value = row
                with self.assertRaises(RuntimeError) as caught:
                    executors._run_nautobot_script(
                        SimpleNamespace(script_path="scripts/off.py"), {}, run, SimpleNamespace(id=1)
                    )
            self.assertEqual(str(caught.exception), SWITCHED_OFF_MESSAGE.format(path="scripts/off.py"))
            self.assertIn("an admin needs to review", str(caught.exception).lower())
            self.assertFalse(marker.exists())


@override_settings(PLUGINS_CONFIG=PLUGINS)
class ScriptApiTestCase(BareRepoMixin, TestCase):
    """Preview for everyone who can edit workflows. Create only with the Write scripts permission."""

    def setUp(self):
        super().setUp()
        self.writer = APIClient()
        grant(self.user, ["write_script"], Workflow)
        self.writer.force_authenticate(self.user)
        self.viewer = User.objects.create(username="script-viewer")
        grant(self.viewer, ["view", "change"], Workflow)
        self.reader = APIClient()
        self.reader.force_authenticate(self.viewer)

    def body(self, **extra):
        data = {
            "name": "Check it",
            "title": "Check it",
            "parameters": [{"name": "device", "type": "object", "model": "dcim.Device"}],
            "body": 'return {"ok": True}',
            "repository": str(self.repo.pk),
        }
        data.update(extra)
        return json.dumps(data)

    def url(self, name):
        return reverse(f"plugins-api:nautobot_workflow_engine-api:{name}")

    def test_preview_is_read_only_without_the_permission(self):
        response = self.reader.post(
            self.url("builder-script-preview"), data=self.body(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["can_write"])
        self.assertNotIn('return {"ok": True}', response.data["content"])
        self.assertIn("Placeholder", response.data["content"])
        self.assertIn("admin needs to review", response.data["note"])
        created = self.reader.post(self.url("builder-script-create"), data=self.body(), content_type="application/json")
        self.assertEqual(created.status_code, 403)
        self.assertIn("Write scripts", created.data["detail"])
        self.assertFalse(WorkflowHelper.objects.filter(file_path="scripts/check_it.py").exists())

    def test_preview_uses_the_body_with_the_permission(self):
        response = self.writer.post(
            self.url("builder-script-preview"), data=self.body(), content_type="application/json"
        )
        self.assertTrue(response.data["can_write"])
        self.assertIn('    return {"ok": True}', response.data["content"])
        self.assertEqual(response.data["path"], "scripts/check_it.py")
        self.assertEqual(response.data["problems"], [])

    def test_create_commits_a_switched_off_script(self):
        response = self.writer.post(
            self.url("builder-script-create"), data=self.body(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["script"]["path"], "scripts/check_it.py")
        self.assertFalse(response.data["script"]["enabled"])
        self.assertIn("an admin needs to review it", response.data["detail"])
        committed = self.show_file("scripts/check_it.py")
        self.assertIn("ENABLED = False", committed)
        self.assertIn("(switched off)", self.tip_fields()["subject"])
        self.assertTrue(WorkflowHelper.objects.filter(source_repo=self.repo, file_path="scripts/check_it.py").exists())
        preview = self.writer.post(
            self.url("builder-script-preview"), data=self.body(), content_type="application/json"
        )
        self.assertIn("already exists", preview.data["problems"][-1]["message"])
        again = self.writer.post(self.url("builder-script-create"), data=self.body(), content_type="application/json")
        self.assertIn(again.status_code, (400, 409))
        self.assertIn("already exists", again.data["detail"])

    def test_create_rejects_bad_code_without_committing(self):
        before = self.commit_count() if self.bare_has_commits() else 0
        response = self.writer.post(
            self.url("builder-script-create"),
            data=self.body(body="import os\nos.system('x'"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data["detail"].startswith("Line 2 of your code:"), response.data)
        self.assertEqual(self.commit_count() if self.bare_has_commits() else 0, before)

    def test_create_explains_an_unready_repository(self):
        self.repo.provided_contents = []
        self.repo.save()
        response = self.writer.post(
            self.url("builder-script-create"), data=self.body(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Couldn't save the script to", response.data["detail"])

    def bare_has_commits(self):
        try:
            self.head()
        except Exception:
            return False
        return True
