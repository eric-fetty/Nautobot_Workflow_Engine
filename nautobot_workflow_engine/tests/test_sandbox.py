"""Sandbox, SafeUser, and golden-output tests for workflow Jinja."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml
from jinja2 import Environment
from jinja2.exceptions import SecurityError

from nautobot_workflow_engine import executors
from nautobot_workflow_engine.executors import _execute_phase_actions, _render
from nautobot_workflow_engine.schema.builder_payload import definition_from_builder
from nautobot_workflow_engine.schema.errors import BuilderValidationError

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "workflows"
ESCAPE_PAYLOADS = (
    "{{ cycler.__init__.__globals__.os.popen('id').read() }}",
    "{{ lipsum.__globals__.os.popen('id').read() }}",
    "{{ joiner.__init__.__globals__.os.popen('id').read() }}",
    "{{ namespace.__init__.__globals__.os.popen('id').read() }}",
    "{{ self.__init__.__globals__.os.popen('id').read() }}",
)


def _sample_context():
    return {
        "inputs": {"hostname": "edge-1", "run_awx": True, "set_mgmt_ip": False, "confirm": True},
        "results": {"add_interface_ip": {"status": "success"}},
        "user": SimpleNamespace(username="ada", password="secret-hash", email="ada@example.com"),
    }


class SandboxEscapeTestCase(unittest.TestCase):
    """The classic Jinja escapes must raise, and they must not run a command."""

    def test_escape_payloads_raise_security_error(self):
        env = executors.get_jinja_environment()
        for payload in ESCAPE_PAYLOADS:
            with self.subTest(payload=payload):
                with patch("os.popen", side_effect=AssertionError("command ran")):
                    with self.assertRaises(SecurityError):
                        env.from_string(payload).render({})

    def test_alters_data_is_blocked(self):
        called = []

        class Bomb:
            def save(self):
                called.append("save")

        Bomb.save.alters_data = True
        env = executors.get_jinja_environment()
        with self.assertRaises(SecurityError):
            env.from_string("{{ bomb.save() }}").render({"bomb": Bomb()})
        self.assertEqual(called, [])

    def test_user_password_renders_empty(self):
        rendered = _render("{{ user.password }}", _sample_context())
        self.assertEqual(rendered, "")
        rendered = _render("{{ user.username }}", _sample_context())
        self.assertEqual(rendered, "ada")

    def test_builder_reports_escape_as_a_field_error(self):
        payload = "{{ cycler.__init__.__globals__.os.popen('id').read() }}"
        with patch("os.popen", side_effect=AssertionError("command ran")):
            with self.assertRaises(BuilderValidationError) as raised:
                definition_from_builder(
                    {
                        "key": "sample",
                        "name": "Sample",
                        "enabled": False,
                        "actions": {
                            "main": [
                                {
                                    "type": "awx",
                                    "name": "run",
                                    "template_id": 1,
                                    "when": payload,
                                    "extra_vars": {"cmd": payload},
                                }
                            ]
                        },
                    }
                )
        self.assertTrue(any("rejected" in item["message"].lower() for item in raised.exception.errors))

    def test_runtime_when_escape_is_a_failed_step(self):
        payload = "{{ cycler.__init__.__globals__.os.popen('id').read() }}"
        action = SimpleNamespace(name="bad", when=payload, action_type="awx")
        queryset = MagicMock()
        queryset.order_by.return_value = queryset
        queryset.count.return_value = 1
        queryset.__iter__.return_value = iter([action])
        workflow_run = SimpleNamespace(
            log="",
            save=MagicMock(),
            workflow=SimpleNamespace(
                source_repo=None, actions=SimpleNamespace(filter=MagicMock(return_value=queryset))
            ),
        )
        with patch("os.popen", side_effect=AssertionError("command ran")):
            summary = _execute_phase_actions(workflow_run, {"inputs": {}, "results": {}}, "main")
        self.assertEqual(summary["results"]["bad"]["status"], "failed")
        self.assertIn("unsafe", summary["results"]["bad"]["error"].lower())


class GoldenTemplateTestCase(unittest.TestCase):
    """Documented workflows render the same way under the sandbox as under Jinja's default environment."""

    def test_example_templates_match(self):
        old = Environment(autoescape=False)
        old.filters.update(executors._get_builtin_filters())
        new = executors.get_jinja_environment()
        context = _sample_context()
        rendered = 0
        for path in sorted(EXAMPLES.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for template in _template_strings(data):
                rendered += 1
                self.assertEqual(
                    _render_or_error(old, template, context),
                    _render_or_error(new, template, executors._prepare_context(context)),
                    path.name,
                )
        self.assertGreaterEqual(rendered, 6)
        self.assertEqual(len(list(EXAMPLES.glob("*.yaml"))), 6)


def _template_strings(value):
    found = []
    if isinstance(value, str) and ("{{" in value or "{%" in value):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_template_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_template_strings(item))
    return found


def _render_or_error(env, template, context):
    try:
        return env.from_string(template).render(context)
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}:{exc}"
