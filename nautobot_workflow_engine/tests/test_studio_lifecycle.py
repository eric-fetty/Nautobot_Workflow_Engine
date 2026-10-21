"""Lifecycle, schema keys, CSV checks, and the visual filter count."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from nautobot.extras.models import Tag
from rest_framework.test import APIClient

from nautobot_workflow_engine.forms import DynamicWorkflowForm, _spreadsheet_formula
from nautobot_workflow_engine.models import Workflow, WorkflowInput, WorkflowRevision
from nautobot_workflow_engine.schema.builder_payload import builder_from_definition, definition_from_builder
from nautobot_workflow_engine.schema.filters import split_filter
from nautobot_workflow_engine.services import (
    ServiceError,
    discard_draft,
    git_stale_banner,
    load_published_into_draft,
    publish,
    restore_revision,
    set_enabled,
    status_label,
)
from nautobot_workflow_engine.tests.test_api_builder import grant

User = get_user_model()


def _definition(key="reboot", **overrides):
    data = {
        "key": key,
        "name": "Reboot a device",
        "description": "Live",
        "inputs": [{"key": "hostname", "label": "Hostname", "type": "string"}],
        "actions": [{"type": "awx", "name": "run", "template_id": 3}],
    }
    data.update(overrides)
    return data


class StudioLifecycleTestCase(TestCase):
    """One shared draft, publish gating, and the three status sentences."""

    def setUp(self):
        self.user = User.objects.create(username="studio-editor")
        grant(self.user, ["view", "add", "change", "publish", "author_code"], Workflow)
        self.workflow = Workflow.objects.create(
            key="reboot",
            name="Reboot a device",
            enabled=True,
            raw_definition=_definition(),
            git_commit="abc123",
        )
        published = WorkflowRevision.objects.create(
            workflow=self.workflow,
            number=2,
            state=WorkflowRevision.STATE_PUBLISHED,
            source=WorkflowRevision.SOURCE_GIT,
            definition=_definition(),
            yaml="name: Live\n",
            content_hash="live",
            git_author="Jane Ortiz",
            published_at=timezone.now(),
        )
        self.draft = WorkflowRevision.objects.create(
            workflow=self.workflow,
            number=3,
            state=WorkflowRevision.STATE_DRAFT,
            source=WorkflowRevision.SOURCE_BUILDER,
            definition=_definition(description="Draft edit"),
            yaml="name: Draft\n",
            content_hash="draft",
            created_by=self.user,
        )
        self.workflow.published_revision = published
        self.workflow.save(update_fields=["published_revision"])

    def test_status_wording_and_stale_banner(self):
        text, kind = status_label(self.workflow, self.draft)
        self.assertEqual(text, "Published v2 · draft changes")
        self.assertEqual(kind, "draft")
        banner = git_stale_banner(self.workflow, self.draft)
        self.assertTrue(banner["detail"].startswith("Updated in Git by Jane Ortiz at "))
        fresh = _definition()
        self.draft.definition = fresh
        self.draft.yaml = "name: Live\n"
        self.draft.content_hash = "live"
        self.draft.base_revision = self.workflow.published_revision
        self.draft.save()
        text, kind = status_label(self.workflow, self.draft)
        self.assertEqual(text, "Published v2 · up to date")
        self.assertEqual(kind, "live")
        self.assertIsNone(git_stale_banner(self.workflow, self.draft))

    def test_publish_is_blocked_while_git_is_newer(self):
        with self.assertRaises(ServiceError) as raised:
            publish(
                user=self.user,
                workflow=self.workflow,
                definition=_definition(description="Draft edit"),
                base_revision=3,
                require_base=False,
            )
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.payload["code"], "stale_git")
        self.assertIn("Updated in Git by Jane Ortiz at ", raised.exception.payload["detail"])

    def test_load_and_restore_stay_in_the_draft(self):
        published_id = self.workflow.published_revision_id
        loaded = load_published_into_draft(user=self.user, workflow=self.workflow)
        self.assertEqual(loaded.state, WorkflowRevision.STATE_DRAFT)
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.published_revision_id, published_id)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.state, WorkflowRevision.STATE_DISCARDED)
        restored = restore_revision(user=self.user, workflow=self.workflow, revision=self.draft)
        self.assertEqual(restored.state, WorkflowRevision.STATE_DRAFT)
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.published_revision_id, published_id)

    def test_discard_and_enable_do_not_touch_git(self):
        discard_draft(user=self.user, workflow=self.workflow)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.state, WorkflowRevision.STATE_DISCARDED)
        before = self.workflow.git_commit
        set_enabled(user=self.user, workflow=self.workflow, enabled=False)
        self.workflow.refresh_from_db()
        self.assertFalse(self.workflow.enabled)
        self.assertEqual(self.workflow.git_commit, before)
        unpublished = Workflow.objects.create(key="new-flow", name="New", raw_definition=_definition(key="new-flow"))
        with self.assertRaises(ServiceError) as raised:
            set_enabled(user=self.user, workflow=unpublished, enabled=True)
        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(raised.exception.payload["detail"], "Publish it first.")

    def test_unpublished_workflow_is_on_the_editor_list(self):
        Workflow.objects.create(
            key="draft-only",
            name="Draft only",
            enabled=False,
            raw_definition=_definition(key="draft-only", name="Draft only"),
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Draft (not published yet)", body)
        self.assertIn("Published v2 · draft changes", body)
        self.assertIn("Edit", body)
        self.assertIn("New workflow", body)
        self.assertNotIn("Launch Workflow", body)
        self.assertNotIn("Managed in Git", body)
        self.assertNotIn("Adopt into Nautobot", body)


class SchemaKeyTestCase(TestCase):
    """New keys round-trip, and existing Django lookups are left alone."""

    def test_label_limits_and_django_lookups(self):
        definition = {
            "key": "vlans",
            "name": "VLANs",
            "inputs": [
                {
                    "key": "vlan_id",
                    "label": "VLAN ID",
                    "type": "integer",
                    "min_value": 1,
                    "max_value": 4094,
                },
                {
                    "key": "device",
                    "label": "Device",
                    "type": "object",
                    "model": "dcim.Device",
                    "filter": {"query": {"name__icontains": "edge"}},
                    "csv_import": False,
                },
            ],
            "actions": [
                {"type": "nautobot", "name": "check", "label": "check", "script": "scripts/check.py"},
                {"type": "nautobot", "name": "note", "label": "Add a note", "script": "scripts/note.py"},
            ],
        }
        payload = builder_from_definition(definition)
        self.assertEqual(payload["inputs"][1]["filter_mode"], "jinja")
        raw = definition_from_builder(payload)
        self.assertNotIn("label", raw["actions"][0])
        self.assertEqual(raw["actions"][1]["label"], "Add a note")
        self.assertEqual(raw["inputs"][0]["min_value"], 1)
        self.assertEqual(raw["inputs"][0]["max_value"], 4094)
        self.assertEqual(raw["inputs"][1]["csv_import"], False)
        self.assertNotIn("csv_import", raw["inputs"][0])
        self.assertEqual(raw["inputs"][1]["filter"]["query"]["name__icontains"], "edge")
        include, exclude = split_filter({"role__n": "access", "name__ic": "edge", "status": ["Active"]})
        self.assertEqual(exclude, {"role": "access"})
        self.assertEqual(include["name__icontains"], "edge")
        self.assertEqual(include["status__in"], ["Active"])


class CsvValidationTestCase(TestCase):
    """Server-side table checks match the launch-form rules."""

    def test_formula_cells_and_ranges(self):
        self.assertEqual(_spreadsheet_formula('=HYPERLINK("http://evil")', "string"), "=")
        self.assertEqual(_spreadsheet_formula("+cmd", "string"), "+")
        self.assertEqual(_spreadsheet_formula("-12", "integer"), "")
        self.assertEqual(_spreadsheet_formula("-1.5", "number"), "")
        workflow = Workflow.objects.create(key="vlans", name="VLANs", raw_definition={"key": "vlans", "name": "VLANs"})
        WorkflowInput.objects.create(
            workflow=workflow,
            key="vlans",
            label="VLANs",
            input_type="listdict",
            columns=[
                {"key": "vlan_id", "label": "VLAN ID", "type": "integer", "min_value": 1, "max_value": 4094},
                {"key": "name", "label": "Name", "type": "string"},
            ],
        )
        rows = [
            {"vlan_id": 300, "name": "store-01"},
            {"vlan_id": 302, "name": '=HYPERLINK("http://evil")'},
            {"vlan_id": 5000, "name": "store-05"},
            {"vlan_id": -4, "name": "negative"},
        ]
        form = DynamicWorkflowForm(workflow, data={"vlans": json.dumps(rows)})
        self.assertFalse(form.is_valid())
        messages = " ".join(form.errors.get("vlans", []))
        self.assertIn('Row 2: Name starts with "=", which looks like a spreadsheet formula', messages)
        self.assertIn("Row 3: VLAN ID must be between 1 and 4094", messages)
        self.assertNotIn("looks like a spreadsheet formula", messages.split("Row 4:")[-1])


class FilterCountTestCase(TestCase):
    """The visual filter builder asks how many objects match."""

    def setUp(self):
        self.user = User.objects.create(username="counter")
        grant(self.user, ["view", "change"], Workflow)
        grant(self.user, ["view"], Tag)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        Tag.objects.create(name="studio-vlan", color="9e9e9e")

    def test_count_and_rejected_lookup(self):
        url = reverse("plugins-api:nautobot_workflow_engine-api:builder-filter-count")
        matched = self.client.post(
            url,
            data=json.dumps({"model": "extras.Tag", "query": {"name": "studio-vlan"}}),
            content_type="application/json",
        )
        self.assertEqual(matched.status_code, 200, matched.content)
        self.assertGreaterEqual(matched.data["count"], 1)
        self.assertIn("studio-vlan", matched.data["names"])
        contains = self.client.post(
            url,
            data=json.dumps({"model": "extras.Tag", "query": {"name__ic": "studio-vlan"}}),
            content_type="application/json",
        )
        self.assertEqual(contains.status_code, 200, contains.content)
        self.assertGreaterEqual(contains.data["count"], 1)
        rejected = self.client.post(
            url,
            data=json.dumps({"model": "extras.Tag", "query": {"name__password": "x"}}),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("not allowed", rejected.data["detail"])
