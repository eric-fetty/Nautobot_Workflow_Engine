"""The User Log and Debug Log panels on a run's page carry Nautobot's copy button."""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nautobot_workflow_engine.datasources import save_workflow_definition
from nautobot_workflow_engine.models import WorkflowRun

User = get_user_model()


def _definition():
    return {
        "key": "log-copy",
        "name": "Log copy",
        "description": "",
        "category": "Lab",
        "approval_required": False,
        "inputs": [{"key": "ticket", "label": "Change ticket", "type": "string"}],
        "actions": [{"type": "awx", "name": "push", "label": "Push", "template_id": 3}],
    }


class RunLogCopyButtonTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.workflow = save_workflow_definition(_definition(), source_repo=None)
        cls.admin = User.objects.create(username="log-copy-admin", is_superuser=True)

    def setUp(self):
        self.client.force_login(self.admin)

    def _page(self, **fields):
        run = WorkflowRun.objects.create(
            workflow=self.workflow, requested_by=self.admin, status="success", inputs={"ticket": "CHG1"}, **fields
        )
        response = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_run", kwargs={"pk": run.pk}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _button(self, content, target):
        match = re.search(
            r'<button type="button" class="btn btn-secondary nb-btn-inline-hover" data-clipboard-target="'
            + re.escape(target)
            + r'">\s*<span aria-hidden="true" class="mdi mdi-content-copy"></span>\s*'
            r'<span class="visually-hidden">([^<]*)</span>\s*</button>',
            content,
        )
        self.assertIsNotNone(match, f"no core copy button for {target}")
        return match

    def test_both_log_panels_have_the_core_copy_button(self):
        content = self._page(user_log="Port Gi1/0/1 is up", log="DEBUG step 1 ok\nDEBUG step 2 ok")
        self.assertEqual(self._button(content, "#run-user-log").group(1), "Copy user log")
        self.assertEqual(self._button(content, "#run-debug-log").group(1), "Copy debug log")
        # The targets hold the log as plain text.
        self.assertIn('<pre id="run-user-log"', content)
        self.assertIn('<pre id="run-debug-log"', content)
        self.assertRegex(content, r'<pre id="run-debug-log"[^>]*>DEBUG step 1 ok\nDEBUG step 2 ok</pre>')

    def test_button_sits_in_the_panel_header(self):
        content = self._page(user_log="hello", log="world")
        for title, target in (("User Log", "#run-user-log"), ("Debug Log", "#run-debug-log")):
            header = re.search(
                r'<div class="card-header d-flex align-items-center">\s*<h5 class="mb-0">\s*<i class="mdi [^"]+"></i> '
                + title
                + r"\s*</h5>\s*<button[^>]*data-clipboard-target=\""
                + re.escape(target),
                content,
            )
            self.assertIsNotNone(header, title)

    def test_targets_exist_while_logs_are_empty(self):
        # A running run fills the logs in place, so the copy targets are always on the page.
        content = self._page(user_log="", log="")
        self._button(content, "#run-user-log")
        self._button(content, "#run-debug-log")
        self.assertIn('<pre id="run-user-log"', content)
        self.assertIn('<pre id="run-debug-log"', content)
