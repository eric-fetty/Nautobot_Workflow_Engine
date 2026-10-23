"""Step conditions can test earlier steps' status and output, at run time and in Preview run."""

# pylint: disable=protected-access

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from nautobot_workflow_engine import executors
from nautobot_workflow_engine.dry_run import dry_run
from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.validate import validate

# Exactly what the builder writes (see js/shared/conditions.js and ui-tests/conditions.test.mjs).
SUCCEEDED = '{{ results.check is defined and results.check.status == "success" }}'
FAILED = '{{ results.check is defined and results.check.status == "failed" }}'
SKIPPED = '{{ results.check is defined and results.check.status == "skipped" }}'
OUTPUT_FIELD = (
    "{{ results.check is defined and results.check.return_value is mapping"
    " and results.check.return_value.reachable == true }}"
)
OUTPUT_WHOLE = "{{ results.count is defined and results.count.return_value == 3 }}"
BRACKET = '{{ results["check-device"] is defined and results["check-device"].status == "failed" }}'


def _render(template, results, strict=False):
    env = executors.get_jinja_environment(None, strict=strict)
    return env.from_string(template).render({"inputs": {}, "results": results})


class ConditionTemplatesTestCase(unittest.TestCase):
    """The generated conditions render as expected in the sandbox, strict or not."""

    def test_status_conditions(self):
        for strict in (False, True):
            self.assertEqual(_render(SUCCEEDED, {"check": {"status": "success"}}, strict), "True")
            self.assertEqual(_render(SUCCEEDED, {"check": {"status": "failed"}}, strict), "False")
            self.assertEqual(_render(FAILED, {"check": {"status": "failed"}}, strict), "True")
            self.assertEqual(_render(SKIPPED, {"check": {"status": "skipped"}}, strict), "True")
            self.assertEqual(_render(BRACKET, {"check-device": {"status": "failed"}}, strict), "True")

    def test_a_step_that_never_ran_is_false_not_an_error(self):
        for strict in (False, True):
            for template in (SUCCEEDED, FAILED, SKIPPED, OUTPUT_FIELD, OUTPUT_WHOLE):
                self.assertEqual(_render(template, {}, strict), "False")

    def test_output_conditions(self):
        for strict in (False, True):
            ok = {"check": {"status": "success", "return_value": {"reachable": True}}}
            down = {"check": {"status": "success", "return_value": {"reachable": False}}}
            empty = {"check": {"status": "success", "return_value": None}}
            self.assertEqual(_render(OUTPUT_FIELD, ok, strict), "True")
            self.assertEqual(_render(OUTPUT_FIELD, down, strict), "False")
            self.assertEqual(_render(OUTPUT_FIELD, empty, strict), "False")
            self.assertEqual(_render(OUTPUT_WHOLE, {"count": {"return_value": 3}}, strict), "True")


class _Actions(list):
    def order_by(self, *fields):
        return _Actions(sorted(self, key=lambda item: item.order))

    def count(self):
        return len(self)


class _Manager:
    def __init__(self, actions):
        self.actions = actions

    def filter(self, **kwargs):
        return _Actions([action for action in self.actions if action.phase == kwargs.get("phase")])


class _Run:
    def __init__(self, actions):
        self.workflow = SimpleNamespace(source_repo=None, actions=_Manager(actions))
        self.log = ""

    def save(self, update_fields=None):
        return None


def _action(name, order, when="", phase="main"):
    return SimpleNamespace(id=order, name=name, phase=phase, order=order, when=when, action_type="nautobot")


class RuntimeEvaluationTestCase(unittest.TestCase):
    """The engine evaluates conditions against results of steps that already ran."""

    def _run(self, actions, outcomes, ctx=None):
        ctx = ctx if ctx is not None else {"inputs": {}, "results": {}}
        ran = []

        def fake(action, _ctx, _run):
            ran.append(action.name)
            return outcomes[action.name]

        with patch.object(executors, "_run_action_with_context", side_effect=fake):
            for phase in ("pre", "main", "post"):
                executors._execute_phase_actions(_Run(actions), ctx, phase=phase)
        return ran, ctx

    def test_conditions_on_status_and_output(self):
        actions = [
            _action("check", 0, phase="pre"),
            _action("reboot", 0, when=OUTPUT_FIELD),
            _action("cleanup", 1, when=FAILED),
            _action(
                "notify", 0, when='{{ results.reboot is defined and results.reboot.status == "success" }}', phase="post"
            ),
        ]
        outcomes = {
            "check": {"status": "success", "return_value": {"reachable": True}},
            "reboot": {"status": "success", "return_value": None},
            "cleanup": {"status": "success", "return_value": None},
            "notify": {"status": "success", "return_value": None},
        }
        ran, ctx = self._run(actions, outcomes)
        self.assertEqual(ran, ["check", "reboot", "notify"])
        self.assertEqual(ctx["results"]["cleanup"]["status"], "skipped")

    def test_skipped_steps_are_visible_to_later_conditions(self):
        actions = [
            _action("check", 0, when="{{ false }}"),
            _action("fallback", 1, when=SKIPPED),
        ]
        ran, ctx = self._run(actions, {"fallback": {"status": "success", "return_value": None}})
        self.assertEqual(ran, ["fallback"])
        self.assertEqual(ctx["results"]["check"], {"status": "skipped", "return_value": None})

    def test_results_from_a_scheduled_before_phase_are_preloaded(self):
        run = SimpleNamespace(requested_by=None)
        phase_results = {
            "pre": {"executed": True, "results": {"check": {"status": "failed", "return_value": None}}},
            "main": {"results": {}},
            "post": {"results": {}},
        }
        with patch.object(executors.SafeUser, "from_user", return_value=None):
            ctx = executors._build_execution_context(run, {}, phase_results)
        self.assertEqual(_render(FAILED, ctx["results"]), "True")


User = get_user_model()


class PreviewRunConditionsTestCase(TestCase):
    """Preview run follows step conditions using the outcome it shows for earlier steps."""

    def setUp(self):
        self.user = User.objects.create_user(username="cond-preview", is_superuser=True)

    def test_preview_assumes_earlier_outcomes(self):
        definition = {
            "key": "cond",
            "name": "Cond",
            "inputs": [{"key": "go", "type": "boolean"}],
            "pre_actions": [{"type": "awx", "name": "check", "template_id": 1}],
            "actions": [
                {"type": "awx", "name": "skipme", "template_id": 2, "when": "{{ inputs.go }}"},
                {"type": "awx", "name": "on_ok", "template_id": 3, "when": SUCCEEDED},
                {"type": "awx", "name": "on_fail", "template_id": 4, "when": FAILED},
                {
                    "type": "awx",
                    "name": "on_skip",
                    "template_id": 5,
                    "when": '{{ results.skipme is defined and results.skipme.status == "skipped" }}',
                },
                {"type": "awx", "name": "on_output", "template_id": 6, "when": OUTPUT_FIELD},
            ],
        }
        result = dry_run(definition, {"go": False}, {}, self.user)
        steps = {step["name"]: step for block in result["phases"] for step in block["steps"]}
        self.assertTrue(steps["on_ok"]["will_run"])
        self.assertFalse(steps["on_fail"]["will_run"])
        self.assertTrue(steps["on_skip"]["will_run"])
        self.assertFalse(steps["on_output"]["will_run"])
        self.assertIn("can’t know", steps["on_output"]["assumption"])
        self.assertEqual(steps["on_ok"]["assumption"], "")

    def test_mocked_results_win_over_assumptions(self):
        definition = {
            "key": "cond",
            "name": "Cond",
            "actions": [
                {"type": "awx", "name": "check", "template_id": 1},
                {"type": "awx", "name": "on_output", "template_id": 6, "when": OUTPUT_FIELD},
            ],
        }
        mocks = {"check": {"status": "success", "return_value": {"reachable": True}}}
        result = dry_run(definition, {}, mocks, self.user)
        steps = {step["name"]: step for block in result["phases"] for step in block["steps"]}
        self.assertTrue(steps["on_output"]["will_run"])


class StepOrderValidationTestCase(unittest.TestCase):
    def _codes(self, actions, pre=None):
        definition = {"key": "cond", "name": "Cond", "actions": actions, "pre_actions": pre or []}
        return [(item["code"], item["message"]) for item in validate(normalize(definition))]

    def test_earlier_steps_are_allowed(self):
        codes = self._codes(
            [{"type": "awx", "name": "reboot", "template_id": 2, "when": SUCCEEDED}],
            pre=[{"type": "awx", "name": "check", "template_id": 1}],
        )
        self.assertNotIn("step_order", [code for code, _ in codes])

    def test_later_steps_and_itself_are_rejected_in_plain_words(self):
        codes = self._codes(
            [
                {"type": "awx", "name": "check", "template_id": 1, "when": SUCCEEDED},
                {"type": "awx", "name": "reboot", "label": "Reboot", "template_id": 2},
                {"type": "awx", "name": "first", "template_id": 3},
            ]
        )
        messages = [message for code, message in codes if code == "step_order"]
        self.assertEqual(
            messages, ["Run only if checks this step itself. A condition can only use steps that run before it."]
        )
        codes = self._codes(
            [
                {"type": "awx", "name": "check", "template_id": 1, "when": '{{ results.reboot.status == "success" }}'},
                {"type": "awx", "name": "reboot", "label": "Reboot", "template_id": 2},
            ]
        )
        self.assertIn(
            "“Reboot”, which runs later", " ".join(message for code, message in codes if code == "step_order")
        )
