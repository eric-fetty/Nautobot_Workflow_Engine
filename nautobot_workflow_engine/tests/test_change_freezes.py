"""Change freezes: schedule math, scope, enforcement, override, scope escape, API and pages."""

import datetime as dt
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from nautobot.core.jobs import BulkEditObjects
from nautobot.core.testing import TransactionTestCase, run_job_for_testing
from nautobot.extras.models import GitRepository, Job, Tag
from nautobot.users.models import ObjectPermission
from rest_framework.test import APIClient

from nautobot_workflow_engine import freezes
from nautobot_workflow_engine.datasources import save_workflow_definition, sync_workflows_from_repo
from nautobot_workflow_engine.default_permissions import RUN_WORKFLOWS
from nautobot_workflow_engine.freezes import (
    PRESETS,
    FreezeScopeEscape,
    check_launch,
    check_scope_escape,
    describe_schedule,
    next_windows,
    occurrences,
    schedule_errors,
    window_at,
)
from nautobot_workflow_engine.jobs import ScheduledWorkflowExecutorJob
from nautobot_workflow_engine.models import MANAGED_BY_GIT, ChangeFreeze, Workflow, WorkflowRun
from nautobot_workflow_engine.services import duplicate_workflow
from nautobot_workflow_engine.tasks import prepare_scheduled_workflow_task

User = get_user_model()
UTC = dt.timezone.utc
_SEQ = 0


def utc(*args):
    """A UTC datetime."""
    return dt.datetime(*args, tzinfo=UTC)


def grant(user, actions, model=Workflow, constraints=None):
    """Give ``user`` one object permission."""
    global _SEQ
    _SEQ += 1
    permission = ObjectPermission.objects.create(
        name=f"freeze-test-{_SEQ}", actions=actions, constraints=constraints, enabled=True
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


def make_tag(name):
    """A tag allowed on workflows."""
    tag = Tag.objects.create(name=name, color="2196f3")
    tag.content_types.add(ContentType.objects.get_for_model(Workflow))
    return tag


def definition(key, category="Networking"):
    """A small published workflow definition."""
    return {
        "key": key,
        "name": key.replace("-", " ").title(),
        "description": "",
        "category": category,
        "inputs": [{"key": "reason", "label": "Reason", "type": "string"}],
        "actions": [{"type": "awx", "name": "push", "label": "Push", "template_id": 3}],
    }


def live_workflow(key, category="Networking"):
    """An enabled, published workflow."""
    workflow = save_workflow_definition(definition(key, category), source_repo=None)
    workflow.enabled = True
    workflow.save()
    return workflow


def one_time(name="Holiday freeze", start=None, end=None, **scope):
    """A one-time freeze, active now by default, covering every workflow unless scope is given."""
    now = timezone.now()
    tags = scope.pop("tags", [])
    workflows = scope.pop("workflows", [])
    excluded = scope.pop("excluded", [])
    if not (scope.get("categories") or tags or workflows):
        scope.setdefault("applies_to_all", True)
    freeze = ChangeFreeze.objects.create(
        name=name,
        message=scope.pop("message", "No changes over the holidays."),
        kind="one_time",
        starts_at=start or now - dt.timedelta(hours=1),
        ends_at=end or now + dt.timedelta(hours=3),
        **scope,
    )
    freeze.match_tags.set(tags)
    freeze.workflows.set(workflows)
    freeze.excluded_workflows.set(excluded)
    return freeze


def recurring(rule, start_date, start_time, minutes, tz="UTC", until=None):
    """An unsaved recurring freeze for the schedule maths."""
    return SimpleNamespace(
        kind="recurring",
        timezone=tz,
        rrule=rule,
        recur_start_date=start_date,
        recur_start_time=start_time,
        duration_minutes=minutes,
        recur_until=until,
        warning_lead_minutes=120,
        enabled=True,
        name="Rule",
        message="",
    )


class OccurrenceMathTestCase(SimpleTestCase):
    """RRULE + length + time zone, including daylight saving changes."""

    def test_weekend_preset_across_spring_forward_in_chicago(self):
        preset = PRESETS["weekend"]
        freeze = recurring(
            preset["rrule"], dt.date(2026, 1, 2), dt.time(17, 0), preset["duration_minutes"], "America/Chicago"
        )
        window = occurrences(freeze, utc(2026, 3, 6), utc(2026, 3, 7))[0]
        # Fri 17:00 CST -> Mon 06:00 CDT: the wall clock is kept, so the window is an hour shorter.
        self.assertEqual(window.start, utc(2026, 3, 6, 23, 0))
        self.assertEqual(window.end, utc(2026, 3, 9, 11, 0))

    def test_weekend_preset_across_fall_back_in_chicago(self):
        preset = PRESETS["weekend"]
        freeze = recurring(
            preset["rrule"], dt.date(2026, 1, 2), dt.time(17, 0), preset["duration_minutes"], "America/Chicago"
        )
        window = occurrences(freeze, utc(2026, 10, 30), utc(2026, 10, 31))[0]
        self.assertEqual(window.start, utc(2026, 10, 30, 22, 0))
        self.assertEqual(window.end, utc(2026, 11, 2, 12, 0))

    def test_nightly_preset_across_spring_forward_in_london(self):
        preset = PRESETS["nightly"]
        freeze = recurring(
            preset["rrule"], dt.date(2026, 1, 1), dt.time(22, 0), preset["duration_minutes"], "Europe/London"
        )
        window = window_at(freeze, utc(2026, 3, 29, 3, 0))
        self.assertEqual(window.start, utc(2026, 3, 28, 22, 0))  # 22:00 GMT
        self.assertEqual(window.end, utc(2026, 3, 29, 5, 0))  # 06:00 BST

    def test_skipped_and_repeated_hours_in_london(self):
        freeze = recurring("FREQ=DAILY", dt.date(2026, 1, 1), dt.time(1, 30), 30, "Europe/London")
        # 01:30 doesn't exist on Mar 29: it moves past the jump (fold=0 uses the GMT offset).
        spring = occurrences(freeze, utc(2026, 3, 29), utc(2026, 3, 29, 12))[0]
        self.assertEqual(spring.start, utc(2026, 3, 29, 1, 30))
        # 01:30 happens twice on Oct 25: the first (BST) one is used.
        autumn = occurrences(freeze, utc(2026, 10, 25), utc(2026, 10, 25, 12))[0]
        self.assertEqual(autumn.start, utc(2026, 10, 25, 0, 30))

    def test_yearly_holiday_preset_spans_new_year(self):
        preset = PRESETS["holidays"]
        freeze = recurring(
            preset["rrule"], dt.date(2026, 1, 1), dt.time(0, 0), preset["duration_minutes"], "America/Chicago"
        )
        window = window_at(freeze, utc(2027, 1, 1, 12))
        self.assertEqual(window.start, utc(2026, 12, 15, 6, 0))
        self.assertEqual(window.end, utc(2027, 1, 5, 6, 0))
        self.assertIsNone(window_at(freeze, utc(2027, 1, 5, 6, 0)))

    def test_monthly_last_weekend_preset(self):
        preset = PRESETS["month_end"]
        freeze = recurring(preset["rrule"], dt.date(2026, 1, 1), dt.time(0, 0), preset["duration_minutes"])
        starts = [window.start for window in occurrences(freeze, utc(2026, 11, 3), utc(2027, 1, 1))]
        self.assertEqual(starts, [utc(2026, 11, 28), utc(2026, 12, 26)])

    def test_recur_until_stops_the_rule(self):
        freeze = recurring("FREQ=WEEKLY;BYDAY=FR", dt.date(2026, 1, 2), dt.time(17, 0), 60, until=dt.date(2026, 1, 16))
        starts = [window.start for window in occurrences(freeze, utc(2026, 1, 1), utc(2026, 3, 1))]
        self.assertEqual(starts, [utc(2026, 1, 2, 17), utc(2026, 1, 9, 17), utc(2026, 1, 16, 17)])

    def test_preview_shows_five_windows(self):
        freeze = recurring("FREQ=DAILY", dt.date(2026, 1, 1), dt.time(22, 0), 60)
        windows = next_windows(freeze, utc(2026, 6, 1, 12), count=5)
        self.assertEqual(len(windows), 5)
        self.assertEqual(windows[0].start, utc(2026, 6, 1, 22))

    def test_plain_language_schedule(self):
        preset = PRESETS["weekend"]
        freeze = recurring(
            preset["rrule"], dt.date(2026, 1, 2), dt.time(17, 0), preset["duration_minutes"], "America/Chicago"
        )
        self.assertEqual(describe_schedule(freeze), "Every Fri 17:00 \u2013 Mon 06:00, America/Chicago")
        nightly = recurring("FREQ=DAILY", dt.date(2026, 1, 1), dt.time(22, 0), 480)
        self.assertEqual(describe_schedule(nightly), "Every day 22:00 \u2013 06:00, UTC")

    def test_schedule_validation(self):
        bad = SimpleNamespace(
            kind="one_time",
            timezone="Mars/Base",
            warning_lead_minutes=-1,
            starts_at=utc(2026, 1, 2),
            ends_at=utc(2026, 1, 1),
        )
        errors = schedule_errors(bad)
        self.assertEqual(set(errors), {"timezone", "warning_lead_minutes", "ends_at"})
        rule = recurring("FREQ=SOMETIMES", dt.date(2026, 1, 1), dt.time(0, 0), 0)
        errors = schedule_errors(rule)
        self.assertIn("rrule", errors)
        self.assertIn("duration_minutes", errors)
        self.assertIn("rrule", schedule_errors(recurring("FREQ=MINUTELY", dt.date(2026, 1, 1), dt.time(0, 0), 5)))
        self.assertEqual(
            schedule_errors(recurring("FREQ=WEEKLY;BYDAY=FR", dt.date(2026, 1, 1), dt.time(17, 0), 60)), {}
        )
        too_long = SimpleNamespace(
            kind="one_time", timezone="UTC", warning_lead_minutes=0, starts_at=utc(2026, 1, 1), ends_at=utc(2027, 2, 1)
        )
        self.assertIn("ends_at", schedule_errors(too_long))


class FreezeScopeTestCase(TestCase):
    """All workflows, categories, tags, listed workflows and exclusions."""

    def setUp(self):
        self.net = live_workflow("net-flow", "Networking")
        self.srv = live_workflow("srv-flow", "Servers")
        self.tag = make_tag("Core")
        self.srv.tags.add(self.tag)

    def _affected(self, freeze):
        return sorted(workflow.key for workflow in freezes.affected_workflows(freeze))

    def test_scope_kinds_and_exclusions(self):
        self.assertEqual(self._affected(one_time("All")), ["net-flow", "srv-flow"])
        self.assertEqual(self._affected(one_time("Cat", categories=["networking"])), ["net-flow"])
        self.assertEqual(self._affected(one_time("Tag", tags=[self.tag])), ["srv-flow"])
        self.assertEqual(self._affected(one_time("One", workflows=[self.net])), ["net-flow"])
        self.assertEqual(self._affected(one_time("Minus", excluded=[self.net])), ["srv-flow"])

    def test_disabled_freeze_blocks_nothing(self):
        one_time("Off", enabled=False)
        self.assertFalse(check_launch(self.net, User.objects.create(username="x")).frozen)

    def test_state_and_lead_time_default(self):
        now = timezone.now()
        active = one_time("Active")
        upcoming = one_time("Later", start=now + dt.timedelta(days=2), end=now + dt.timedelta(days=3))
        expired = one_time("Past", start=now - dt.timedelta(days=3), end=now - dt.timedelta(days=2))
        disabled = one_time("Off", enabled=False)
        self.assertEqual(
            [freezes.freeze_state(item) for item in (active, upcoming, expired, disabled)],
            ["active", "upcoming", "expired", "disabled"],
        )
        self.assertEqual(active.warning_lead_minutes, 120)


class LaunchEnforcementTestCase(TestCase):
    """Nothing starts or is scheduled during a freeze; override needs the permission and a reason."""

    def setUp(self):
        self.workflow = live_workflow("net-flow")
        self.user = User.objects.create(username="runner")
        ObjectPermission.objects.get(name=RUN_WORKFLOWS).users.add(self.user)
        self.client.force_login(self.user)
        self.url = reverse("plugins:nautobot_workflow_engine:workflow_launch", kwargs={"key": self.workflow.key})

    def _post(self, **extra):
        data = {"reason": "maintenance", "execution_type": "immediate", "scheduled_datetime": ""}
        data.update(extra)
        return self.client.post(self.url, data)

    def _schedule(self, when, **extra):
        return self._post(
            execution_type="scheduled",
            scheduled_datetime=when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M"),
            browser_timezone_offset_minutes="0",
            **extra,
        )

    @patch("nautobot_workflow_engine.tasks.execute_workflow_task.delay")
    def test_immediate_launch_is_refused(self, delay):
        one_time()
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Holiday freeze")
        self.assertFalse(WorkflowRun.objects.exists())
        delay.assert_not_called()

    @patch("nautobot_workflow_engine.tasks.prepare_scheduled_workflow_task.delay")
    def test_scheduling_into_a_freeze_is_refused(self, delay):
        now = timezone.now()
        one_time(start=now + dt.timedelta(days=2), end=now + dt.timedelta(days=3))
        response = self._schedule(now + dt.timedelta(days=2, hours=2))
        self.assertContains(response, "The time you picked is inside it")
        self.assertFalse(WorkflowRun.objects.exists())
        delay.assert_not_called()

    @patch("nautobot_workflow_engine.tasks.prepare_scheduled_workflow_task.delay")
    def test_scheduling_while_a_freeze_is_active_is_refused_so_pre_actions_do_not_run(self, delay):
        one_time()
        response = self._schedule(timezone.now() + dt.timedelta(days=10))
        self.assertContains(response, "pre-actions")
        self.assertFalse(WorkflowRun.objects.exists())
        delay.assert_not_called()

    @patch("nautobot_workflow_engine.tasks.prepare_scheduled_workflow_task.delay")
    def test_scheduling_outside_a_freeze_works(self, delay):
        now = timezone.now()
        one_time(start=now + dt.timedelta(days=2), end=now + dt.timedelta(days=3))
        response = self._schedule(now + dt.timedelta(days=5))
        self.assertEqual(response.status_code, 302)
        delay.assert_called_once()

    @patch("nautobot_workflow_engine.tasks.prepare_scheduled_workflow_task.delay")
    @patch("nautobot_workflow_engine.views.integration_name_for_workflow", return_value="ServiceNow")
    @patch("nautobot_workflow_engine.views.get_change_window")
    def test_servicenow_window_inside_a_freeze_is_refused(self, change_window, _name, delay):
        now = timezone.now()
        one_time(start=now + dt.timedelta(days=2), end=now + dt.timedelta(days=3))
        change_window.return_value = SimpleNamespace(
            window_start=now + dt.timedelta(days=2, hours=1),
            window_end=now + dt.timedelta(days=2, hours=4),
            change_number="CHG0012345",
            sys_id="abc",
        )
        with patch.object(Workflow, "servicenow_change_input_key", new_callable=PropertyMock, return_value="reason"):
            response = self._post(reason="CHG0012345", execution_type="servicenow")
        self.assertContains(response, "Holiday freeze")
        self.assertFalse(WorkflowRun.objects.exists())
        delay.assert_not_called()

    def test_launch_page_is_locked_for_people_without_override(self):
        one_time()
        page = self.client.get(self.url)
        self.assertContains(page, "Change freeze in effect: Holiday freeze")
        self.assertContains(page, "Frozen")
        self.assertNotContains(page, 'name="freeze_override_reason"')

    @patch("nautobot_workflow_engine.tasks.execute_workflow_task.delay")
    def test_override_needs_a_reason_and_is_stamped(self, delay):
        one_time()
        grant(self.user, ["override_freeze"])
        user = User.objects.get(pk=self.user.pk)
        self.client.force_login(user)
        page = self.client.get(self.url)
        self.assertContains(page, 'name="freeze_override_reason"')
        self.assertContains(page, "Override and run")
        refused = self._post(freeze_override_reason="   ")
        self.assertContains(refused, "type a reason")
        self.assertFalse(WorkflowRun.objects.exists())
        response = self._post(freeze_override_reason="Sev1 outage fix")
        self.assertEqual(response.status_code, 302)
        run = WorkflowRun.objects.get()
        self.assertEqual(run.freeze_override_by, user)
        self.assertEqual(run.freeze_override_reason, "Sev1 outage fix")
        self.assertEqual(run.freeze_name, "Holiday freeze")
        self.assertIn("overridden by runner: Sev1 outage fix", run.log)
        delay.assert_called_once()

    def test_override_constraint_limits_which_workflows(self):
        other = live_workflow("srv-flow", "Servers")
        one_time()
        grant(self.user, ["override_freeze"], constraints={"category": "Servers"})
        user = User.objects.get(pk=self.user.pk)
        self.assertTrue(check_launch(other, user, override_reason="ok").overridden)
        self.assertTrue(check_launch(self.workflow, user, override_reason="ok").blocked)

    def test_dry_run_and_runs_in_progress_are_not_touched(self):
        running = WorkflowRun.objects.create(
            workflow=self.workflow, requested_by=self.user, inputs={}, status="running"
        )
        one_time()
        running.refresh_from_db()
        self.assertEqual(running.status, "running")
        admin = User.objects.create(username="builder", is_superuser=True)
        client = APIClient()
        client.force_authenticate(admin)
        response = client.post(
            reverse("plugins-api:nautobot_workflow_engine-api:workflow-dry-run", kwargs={"key": self.workflow.key}),
            data=json.dumps({"inputs": {"reason": "x"}}),
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 403)
        self.assertNotIn("freeze", json.dumps(response.data).lower())


class FireTimeEnforcementTestCase(TestCase):
    """Runs scheduled before a freeze are Blocked by freeze when they come due."""

    def setUp(self):
        self.workflow = live_workflow("net-flow")
        self.user = User.objects.create(username="scheduler")
        ObjectPermission.objects.get(name=RUN_WORKFLOWS).users.add(self.user)

    def _run(self, status="scheduled", **extra):
        return WorkflowRun.objects.create(
            workflow=self.workflow,
            requested_by=self.user,
            inputs={"reason": "x"},
            status=status,
            scheduled_for=timezone.now(),
            **extra,
        )

    @patch("nautobot_workflow_engine.tasks.execute_workflow_task.delay")
    def test_scheduled_run_is_blocked_not_canceled(self, delay):
        run = self._run()
        freeze = one_time()
        ScheduledWorkflowExecutorJob().run(workflow_run_id=str(run.pk))
        run.refresh_from_db()
        self.assertEqual(run.status, "blocked")
        self.assertEqual(run.get_status_display(), "Blocked by freeze")
        self.assertEqual(run.freeze, freeze)
        self.assertIsNone(run.scheduled_job)
        self.assertIn("Blocked by change freeze", run.user_log)
        delay.assert_not_called()

    @patch("nautobot_workflow_engine.tasks.execute_workflow_task.delay")
    def test_approved_run_that_fires_during_a_freeze_is_blocked(self, delay):
        run = self._run(status="pending_approval")
        one_time()
        ScheduledWorkflowExecutorJob().run(workflow_run_id=str(run.pk))
        run.refresh_from_db()
        self.assertEqual(run.status, "blocked")
        delay.assert_not_called()

    @patch("nautobot_workflow_engine.tasks.execute_workflow_task.delay")
    def test_scheduled_override_counts_only_while_the_permission_is_held(self, delay):
        permission = grant(self.user, ["override_freeze"])
        one_time()
        kept = self._run(freeze_override_by=self.user, freeze_override_reason="CAB approved")
        ScheduledWorkflowExecutorJob().run(workflow_run_id=str(kept.pk))
        kept.refresh_from_db()
        self.assertEqual(kept.status, "running")
        delay.assert_called_once()
        permission.delete()
        lost = self._run(freeze_override_by=self.user, freeze_override_reason="CAB approved")
        ScheduledWorkflowExecutorJob().run(workflow_run_id=str(lost.pk))
        lost.refresh_from_db()
        self.assertEqual(lost.status, "blocked")
        self.assertIn("no longer has Override change freezes", lost.log)

    @patch("nautobot_workflow_engine.tasks.run_pre_actions_for_scheduled_run")
    @patch("nautobot_workflow_engine.tasks.create_scheduled_job_for_run")
    def test_prepare_task_blocks_before_pre_actions(self, create_job, pre_actions):
        run = self._run(status="running")
        run.scheduled_for = timezone.now() + dt.timedelta(days=10)
        run.save()
        one_time()
        prepare_scheduled_workflow_task.run(str(run.pk))
        run.refresh_from_db()
        self.assertEqual(run.status, "blocked")
        pre_actions.assert_not_called()
        create_job.assert_not_called()

    def test_run_workflow_backstop(self):
        from nautobot_workflow_engine.executors import run_workflow
        from nautobot_workflow_engine.freezes import FreezeBlocked

        one_time()
        run = self._run(status="running")
        with self.assertRaises(FreezeBlocked):
            run_workflow(self.workflow, self.user, {}, run)
        run.refresh_from_db()
        self.assertEqual(run.status, "blocked")


class ScopeEscapeTestCase(TestCase):
    """Edits that would take a workflow out of a freeze need Override change freezes."""

    def setUp(self):
        self.user = User.objects.create(username="editor")
        grant(self.user, ["view", "add", "change", "publish"])
        grant(self.user, ["view"], model=Tag)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.core = make_tag("Core")
        self.extra = make_tag("Extra")
        self.assertEqual(self._send("post", "workflow-list", {"definition": definition("net-flow")}).status_code, 201)
        published = self._send(
            "post",
            "workflow-publish",
            {"definition": definition("net-flow"), "base_revision": 1, "enable": True},
            key="net-flow",
        )
        self.assertEqual(published.status_code, 202, published.content)
        self.workflow = Workflow.objects.get(key="net-flow")
        self.workflow.tags.add(self.core)

    def _send(self, method, name, body, **kwargs):
        url = reverse(f"plugins-api:nautobot_workflow_engine-api:{name}", kwargs=kwargs or None)
        return getattr(self.api, method)(url, data=json.dumps(body), content_type="application/json")

    def _draft(self, **body):
        self.workflow.refresh_from_db()
        head = (self.workflow.draft_revision or self.workflow.published_revision).number
        body.setdefault("definition", definition("net-flow"))
        return self._send("put", "workflow-draft", {"base_revision": head, **body}, key="net-flow")

    def test_tag_removal_that_escapes_is_refused_without_override(self):
        one_time("Tag freeze", tags=[self.core])
        response = self._draft(tags=["Extra"])
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["errors"][0]["pointer"], "/tags")
        self.assertIn("Tag freeze", response.data["errors"][0]["message"])
        self.assertEqual(list(self.workflow.tags.values_list("name", flat=True)), ["Core"])

    def test_upcoming_freeze_counts_and_override_allows_it(self):
        now = timezone.now()
        one_time("Later", start=now + dt.timedelta(days=5), end=now + dt.timedelta(days=6), tags=[self.core])
        self.assertEqual(self._draft(tags=[]).status_code, 400)
        grant(self.user, ["override_freeze"])
        self.api.force_authenticate(User.objects.get(pk=self.user.pk))
        response = self._draft(tags=[])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(self.workflow.tags.exists())

    def test_changes_that_keep_it_in_scope_or_only_add_are_allowed(self):
        one_time("Tag freeze", tags=[self.core])
        self.assertEqual(self._draft(tags=["Core", "Extra"]).status_code, 200)
        one_time("Both", tags=[self.extra], categories=["Networking"])
        # Still covered by the category, and Core keeps it in Tag freeze.
        self.assertEqual(self._draft(tags=["Core"]).status_code, 200)
        expired_only = Workflow.objects.get(key="net-flow")
        self.assertEqual(sorted(expired_only.tags.values_list("name", flat=True)), ["Core"])

    def test_tag_and_category_change_together_are_checked_together(self):
        one_time("Cat", categories=["Networking"])
        one_time("Tag", tags=[self.core])
        with self.assertRaises(FreezeScopeEscape):
            check_scope_escape(self.workflow, proposed_tags=[], proposed_category="Servers", user=self.user)
        self.assertEqual(
            check_scope_escape(
                self.workflow, proposed_tags=[self.core], proposed_category="networking", user=self.user
            ),
            [],
        )

    def test_category_change_on_draft_and_publish_is_refused(self):
        one_time("Net freeze", categories=["Networking"])
        moved = definition("net-flow", "Servers")
        response = self._draft(definition=moved)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["errors"][0]["pointer"], "/category")
        response = self._send(
            "post",
            "workflow-publish",
            {"definition": moved, "base_revision": self.workflow.published_revision.number, "enable": True},
            key="net-flow",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.category, "Networking")

    def test_applies_to_all_cannot_be_escaped_by_editing(self):
        one_time("Everything")
        self.assertEqual(self._draft(tags=[], definition=definition("net-flow", "Servers")).status_code, 200)

    def test_git_sync_skips_a_category_change_that_escapes(self):
        repo = GitRepository.objects.create(name="Freeze Repo", remote_url="https://example.com/f.git")
        workflow = Workflow.objects.create(
            key="git-flow",
            name="Git Flow",
            category="Networking",
            enabled=True,
            managed_by=MANAGED_BY_GIT,
            source_repo=repo,
            raw_definition=definition("git-flow"),
        )
        one_time("Net freeze", categories=["Networking"])
        lines = []
        job_result = SimpleNamespace(log=lambda message="", **kwargs: lines.append(message))
        text = "key: git-flow\nname: Moved\ncategory: Servers\nenabled: true\n"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "workflows").mkdir()
            (Path(tmp) / "workflows" / "git-flow.yaml").write_text(text, encoding="utf-8")
            with patch.object(GitRepository, "filesystem_path", property(lambda _self: tmp)):
                sync_workflows_from_repo(repo, job_result, delete=False)
        workflow.refresh_from_db()
        self.assertEqual((workflow.name, workflow.category, workflow.enabled), ("Git Flow", "Networking", True))
        self.assertTrue(any("E-NWE-302" in line and "Net freeze" in line for line in lines), lines)


class BulkTagEditFreezeTestCase(TransactionTestCase):
    """Bulk edit skips rows whose tag removal would escape a freeze."""

    def setUp(self):
        super().setUp()
        from nautobot.extras.utils import refresh_job_model_from_job_class

        refresh_job_model_from_job_class(Job, BulkEditObjects)
        self.user = User.objects.create(username="bulk-editor")
        grant(self.user, ["view", "change"])
        grant(self.user, ["view"], model=Tag)
        self.core = make_tag("Core")
        self.frozen = live_workflow("frozen-flow", "Networking")
        self.free = live_workflow("free-flow", "Servers")
        for workflow in (self.frozen, self.free):
            workflow.tags.add(self.core)
        # free-flow is excluded from the freeze, so removing Core from it takes it out of nothing.
        one_time("Core freeze", tags=[self.core], excluded=[self.free])

    def _run(self):
        pks = [self.frozen.pk, self.free.pk]
        job_model = Job.objects.get_for_class_path(BulkEditObjects.class_path)
        job_form = BulkEditObjects.as_form(
            data={
                "content_type": ContentType.objects.get_for_model(Workflow).pk,
                "form_data": {"pk": [str(pk) for pk in pks], "_nullify": [], "remove_tags": [str(self.core.pk)]},
                "pk_list": [str(pk) for pk in pks],
                "edit_all": False,
                "filter_query_params": {},
            }
        )
        self.assertTrue(job_form.is_valid(), job_form.errors)
        job_kwargs = BulkEditObjects.prepare_job_kwargs(job_form.cleaned_data)
        return run_job_for_testing(
            job_model, username=self.user.username, job_kwargs=BulkEditObjects.serialize_data(job_kwargs)
        )

    def test_escaping_rows_are_skipped_without_override(self):
        result = self._run()
        self.assertEqual(result.status, "SUCCESS", result.result)
        self.assertEqual(list(self.frozen.tags.values_list("name", flat=True)), ["Core"])
        self.assertFalse(self.free.tags.exists())

    def test_override_holder_can_remove_it(self):
        grant(self.user, ["override_freeze"])
        result = self._run()
        self.assertEqual(result.status, "SUCCESS", result.result)
        self.assertFalse(self.frozen.tags.exists())

    def test_the_confirm_step_says_what_will_be_skipped(self):
        self.client.force_login(self.user)
        with patch("nautobot_workflow_engine.views.NautobotUIViewSet.perform_bulk_update") as parent:
            parent.return_value = __import__("django.http", fromlist=["HttpResponse"]).HttpResponse("ok")
            response = self.client.post(
                reverse("plugins:nautobot_workflow_engine:workflow_bulk_edit"),
                {"pk": [self.frozen.pk, self.free.pk], "remove_tags": [self.core.pk], "_apply": "1"},
                follow=False,
            )
        self.assertEqual(response.status_code, 200)
        stored = [str(message) for message in response.wsgi_request._messages]
        self.assertTrue(
            any("1 skipped because removing the tag" in text and "Frozen Flow" in text for text in stored), stored
        )


class FreezePagesTestCase(TestCase):
    """Menu, list, form, detail, banner, lock badge, run detail and dashboard."""

    def setUp(self):
        self.admin = User.objects.create(username="freeze-admin", is_superuser=True, is_staff=True)
        self.workflow = live_workflow("net-flow")

    def test_menu_list_and_states(self):
        now = timezone.now()
        one_time("Active one")
        one_time("Upcoming one", start=now + dt.timedelta(days=2), end=now + dt.timedelta(days=3))
        one_time("Expired one", start=now - dt.timedelta(days=3), end=now - dt.timedelta(days=2))
        one_time("Disabled one", enabled=False)
        self.client.force_login(self.admin)
        # Nautobot 3 fills list tables over HTMX.
        page = self.client.get(reverse("plugins:nautobot_workflow_engine:changefreeze_list"), HTTP_HX_REQUEST="true")
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        table = body[body.find("<table") : body.find("</table>")]
        for label in ("Active now", "Upcoming", "Expired", "Disabled", "1 workflow"):
            self.assertIn(label, table, table)
        home = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertContains(home, reverse("plugins:nautobot_workflow_engine:changefreeze_list"))
        filtered = self.client.get(
            reverse("plugins:nautobot_workflow_engine:changefreeze_list") + "?state=upcoming", HTTP_HX_REQUEST="true"
        )
        self.assertContains(filtered, "Upcoming one")
        self.assertNotContains(filtered, "Expired one")

    def test_add_form_saves_a_recurring_freeze_in_its_zone(self):
        self.client.force_login(self.admin)
        url = reverse("plugins:nautobot_workflow_engine:changefreeze_add")
        self.assertContains(self.client.get(url), "nwe-freeze-presets")
        response = self.client.post(
            url,
            {
                "name": "Weekends",
                "message": "Weekend freeze",
                "enabled": "on",
                "timezone": "America/Chicago",
                "kind": "recurring",
                "preset": "weekend",
                "rrule": "FREQ=WEEKLY;BYDAY=FR",
                "recur_start_date": "2026-01-02",
                "recur_start_time": "17:00",
                "duration_minutes": "3660",
                "warning_lead_minutes": "",
                "categories": ["Networking"],
            },
        )
        self.assertEqual(response.status_code, 302, response.content.decode()[:2000])
        freeze = ChangeFreeze.objects.get(name="Weekends")
        self.assertEqual(freeze.warning_lead_minutes, 120)
        self.assertEqual(freeze.created_by, self.admin)
        self.assertEqual(describe_schedule(freeze), "Every Fri 17:00 \u2013 Mon 06:00, America/Chicago")
        self.assertEqual(response.url, freeze.get_absolute_url())

    def test_one_time_times_are_read_in_the_freeze_zone_and_scope_is_required(self):
        self.client.force_login(self.admin)
        url = reverse("plugins:nautobot_workflow_engine:changefreeze_add")
        base = {
            "name": "Xmas",
            "timezone": "America/Chicago",
            "kind": "one_time",
            "starts_at": "2026-12-24 17:00",
            "ends_at": "2026-12-26 08:00",
            "warning_lead_minutes": "0",
        }
        response = self.client.post(url, base)
        self.assertContains(response, "Choose what the freeze covers")
        response = self.client.post(url, {**base, "applies_to_all": "on"})
        self.assertEqual(response.status_code, 302)
        freeze = ChangeFreeze.objects.get(name="Xmas")
        self.assertEqual(freeze.starts_at, utc(2026, 12, 24, 23, 0))
        self.assertEqual(freeze.warning_lead_minutes, 0)

    @patch("nautobot_workflow_engine.freeze_views.cancel_scheduled_run")
    def test_detail_lists_runs_in_the_window_and_cancels_them(self, cancel):
        now = timezone.now()
        freeze = one_time(start=now + dt.timedelta(days=2), end=now + dt.timedelta(days=3))
        run = WorkflowRun.objects.create(
            workflow=self.workflow,
            requested_by=self.admin,
            inputs={},
            status="scheduled",
            scheduled_for=now + dt.timedelta(days=2, hours=1),
        )
        WorkflowRun.objects.create(
            workflow=self.workflow,
            requested_by=self.admin,
            inputs={},
            status="scheduled",
            scheduled_for=now + dt.timedelta(days=5),
        )
        self.client.force_login(self.admin)
        page = self.client.get(freeze.get_absolute_url())
        self.assertContains(page, "1 scheduled run fall")
        self.assertContains(page, "Cancel them")
        self.client.post(reverse("plugins:nautobot_workflow_engine:changefreeze_cancel_runs", kwargs={"pk": freeze.pk}))
        cancel.assert_called_once()
        self.assertEqual(cancel.call_args.args[0], run)

    def test_banner_and_lock_show_to_workflow_viewers_without_view_freezes(self):
        one_time(message="Year-end close")
        viewer = User.objects.create(username="viewer")
        ObjectPermission.objects.get(name=RUN_WORKFLOWS).users.add(viewer)
        self.assertFalse(viewer.has_perm("nautobot_workflow_engine.view_changefreeze"))
        self.client.force_login(viewer)
        page = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertContains(page, "Change freeze in effect: Holiday freeze")
        self.assertContains(page, "Year-end close")
        self.assertContains(page, "data-nwe-freeze-lock")
        self.assertContains(page, "Run is off during change freeze")

    def test_banner_warns_within_the_lead_time_and_zero_turns_it_off(self):
        now = timezone.now()
        soon = one_time("Soon", start=now + dt.timedelta(minutes=90), end=now + dt.timedelta(hours=5))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertContains(page, "Change freeze starts soon: Soon")
        soon.warning_lead_minutes = 0
        soon.save()
        page = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertNotContains(page, "Change freeze starts soon")
        soon.warning_lead_minutes = 60
        soon.save()
        self.assertNotContains(
            self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list")), "starts soon"
        )
        windows = freezes.launch_windows(self.workflow)
        self.assertEqual(windows[0]["warning_lead_minutes"], 60)

    def test_run_detail_shows_blocked_and_override(self):
        freeze = one_time()
        run = WorkflowRun.objects.create(
            workflow=self.workflow,
            requested_by=self.admin,
            inputs={},
            status="blocked",
            freeze=freeze,
            freeze_name=freeze.name,
        )
        self.client.force_login(self.admin)
        page = self.client.get(run.get_absolute_url())
        self.assertContains(page, "Blocked by freeze")
        self.assertContains(page, "Holiday freeze")
        stamped = WorkflowRun.objects.create(
            workflow=self.workflow,
            requested_by=self.admin,
            inputs={},
            status="success",
            freeze_override_by=self.admin,
            freeze_override_reason="Sev1",
        )
        self.assertContains(self.client.get(stamped.get_absolute_url()), "Freeze overridden by freeze-admin: Sev1")
        history = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_run_list") + "?status=blocked")
        self.assertContains(history, "Blocked by freeze")

    def test_dashboard_leaves_blocked_and_canceled_runs_out_of_the_success_rate(self):
        for status in ("success", "failed", "blocked", "canceled"):
            WorkflowRun.objects.create(workflow=self.workflow, requested_by=self.admin, inputs={}, status=status)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_report"))
        stats = page.context["workflows_with_stats"]
        row = next(item for item in stats if item["workflow"].pk == self.workflow.pk)
        self.assertEqual(row["total_runs"], 2)
        self.assertEqual(row["successful_runs"], 1)
        self.assertEqual(row["success_rate"], 50.0)

        category_row = next(
            item for item in page.context["success_by_category"] if item["category"] == self.workflow.category
        )
        self.assertEqual(category_row["total"], 2)
        self.assertEqual(category_row["success_rate"], 50.0)

        catalog = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertEqual(catalog.context["catalog_stats"]["runs_30d"], 2)
        self.assertEqual(catalog.context["catalog_stats"]["success_rate"], 50)

    def test_duplicate_copies_tags(self):
        tag = make_tag("Copied")
        self.workflow.tags.add(tag)
        copy, _revision = duplicate_workflow(user=self.admin, source=self.workflow, key="net-copy", name="Net copy")
        self.assertEqual(list(copy.tags.all()), [tag])


class FreezeApiTestCase(TestCase):
    """CRUD under the freeze permissions, the status endpoint and the preview endpoint."""

    def setUp(self):
        self.workflow = live_workflow("net-flow")
        self.manager = User.objects.create(username="freeze-manager")
        grant(self.manager, ["view", "add", "change", "delete"], model=ChangeFreeze)
        grant(self.manager, ["view"])
        self.api = APIClient()
        self.api.force_authenticate(self.manager)
        self.list_url = reverse("plugins-api:nautobot_workflow_engine-api:changefreeze-list")

    def test_crud_and_permissions(self):
        body = {
            "name": "API freeze",
            "kind": "recurring",
            "timezone": "Europe/London",
            "rrule": "FREQ=DAILY",
            "recur_start_date": "2026-01-01",
            "recur_start_time": "22:00:00",
            "duration_minutes": 480,
            "workflows": ["net-flow"],
        }
        response = self.api.post(self.list_url, body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["warning_lead_minutes"], 120)
        self.assertEqual(response.data["schedule"], "Every day 22:00 \u2013 06:00, Europe/London")
        detail = response.data["url"]
        self.assertEqual(self.api.patch(detail, {"enabled": False}, format="json").status_code, 200)
        bad = self.api.post(self.list_url, {**body, "name": "Nothing", "workflows": []}, format="json")
        self.assertEqual(bad.status_code, 400)
        broken = self.api.post(self.list_url, {**body, "name": "Bad rule", "rrule": "NOPE"}, format="json")
        self.assertEqual(broken.status_code, 400)
        self.assertIn("rrule", json.dumps(broken.data))
        outsider = User.objects.create(username="no-freeze-perms")
        client = APIClient()
        client.force_authenticate(outsider)
        self.assertEqual(client.post(self.list_url, {**body, "name": "Nope"}, format="json").status_code, 403)
        self.assertEqual(client.get(self.list_url).status_code, 403)
        self.assertEqual(self.api.delete(detail).status_code, 204)

    def test_status_endpoint(self):
        url = reverse("plugins-api:nautobot_workflow_engine-api:changefreeze-status")
        self.assertFalse(self.api.get(url, {"workflow": "net-flow"}).data["frozen"])
        now = timezone.now()
        one_time("Later", start=now + dt.timedelta(days=1), end=now + dt.timedelta(days=2))
        body = self.api.get(url, {"workflow": "net-flow"}).data
        self.assertFalse(body["frozen"])
        self.assertEqual(body["next"]["name"], "Later")
        at = (now + dt.timedelta(days=1, hours=1)).isoformat()
        body = self.api.get(url, {"workflow": "net-flow", "at": at}).data
        self.assertTrue(body["frozen"])
        self.assertEqual(body["active"][0]["name"], "Later")
        self.assertTrue(self.api.get(url, {"at": at}).data["frozen"])
        self.assertEqual(self.api.get(url, {"workflow": "missing"}).status_code, 404)

    def test_preview_saves_nothing(self):
        now = timezone.now()
        WorkflowRun.objects.create(
            workflow=self.workflow,
            requested_by=self.manager,
            inputs={},
            status="scheduled",
            scheduled_for=now + dt.timedelta(days=1, hours=1),
        )
        url = reverse("plugins-api:nautobot_workflow_engine-api:changefreeze-preview")
        body = {
            "kind": "one_time",
            "timezone": "UTC",
            "starts_at": (now + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
            "ends_at": (now + dt.timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
            "categories": ["Networking"],
        }
        response = self.api.post(url, body, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["errors"], {})
        self.assertEqual(response.data["affected_count"], 1)
        self.assertEqual(response.data["runs_count"], 1)
        self.assertEqual(len(response.data["windows"]), 1)
        self.assertFalse(ChangeFreeze.objects.exists())
        bad = self.api.post(url, {**body, "ends_at": body["starts_at"]}, format="json")
        self.assertIn("ends_at", bad.data["errors"])
        outsider = User.objects.create(username="preview-outsider")
        client = APIClient()
        client.force_authenticate(outsider)
        self.assertEqual(client.post(url, body, format="json").status_code, 403)
