"""Draft and published revision lifecycle."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from nautobot_workflow_engine.models import (
    MANAGED_BY_GIT,
    MANAGED_BY_NAUTOBOT,
    Workflow,
    WorkflowAction,
    WorkflowRevision,
)
from nautobot_workflow_engine.services import publish, update_draft

User = get_user_model()


def _definition(key="lab-flow"):
    return {
        "key": key,
        "name": "Lab flow",
        "category": "lab",
        "inputs": [{"key": "hostname", "label": "Hostname", "type": "string"}],
        "actions": [{"type": "awx", "name": "run", "template_id": 3}],
    }


class RevisionLifecycleTestCase(TestCase):
    """Drafts do not change runtime rows. Publish does."""

    def setUp(self):
        self.user = User.objects.create(username="editor", is_superuser=True)

    def test_draft_does_not_change_runtime_and_publish_does(self):
        workflow, revision = publish(
            user=self.user,
            workflow=None,
            definition=_definition(),
            base_revision=None,
            require_base=False,
        )
        self.assertEqual(revision.number, 1)
        self.assertEqual(revision.state, WorkflowRevision.STATE_PUBLISHED)
        self.assertEqual(workflow.inputs.get().label, "Hostname")
        draft = update_draft(
            user=self.user,
            workflow=workflow,
            definition=_definition() | {"inputs": [{"key": "hostname", "label": "Device", "type": "string"}]},
            base_revision=1,
        )
        workflow.refresh_from_db()
        self.assertEqual(draft.state, WorkflowRevision.STATE_DRAFT)
        self.assertEqual(workflow.inputs.get().label, "Hostname")
        self.assertEqual(workflow.raw_definition["inputs"][0]["label"], "Hostname")
        self.assertTrue(workflow.actions.filter(name="run").exists())

    def test_one_draft_and_outdated_draft(self):
        workflow = Workflow.objects.create(
            key="rev-flow",
            name="Rev",
            raw_definition=_definition("rev-flow"),
            managed_by=MANAGED_BY_NAUTOBOT,
        )
        published = WorkflowRevision.objects.create(
            workflow=workflow,
            number=1,
            state=WorkflowRevision.STATE_PUBLISHED,
            source=WorkflowRevision.SOURCE_BUILDER,
            definition=_definition("rev-flow"),
            yaml="key: rev-flow\n",
            content_hash="a" * 64,
        )
        workflow.published_revision = published
        workflow.save(update_fields=["published_revision"])
        WorkflowRevision.objects.create(
            workflow=workflow,
            number=2,
            state=WorkflowRevision.STATE_DRAFT,
            source=WorkflowRevision.SOURCE_BUILDER,
            definition=_definition("rev-flow"),
            yaml="key: rev-flow\n",
            content_hash="b" * 64,
            base_revision=published,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkflowRevision.objects.create(
                    workflow=workflow,
                    number=3,
                    state=WorkflowRevision.STATE_DRAFT,
                    source=WorkflowRevision.SOURCE_BUILDER,
                    definition=_definition("rev-flow"),
                    yaml="key: rev-flow\n",
                    content_hash="c" * 64,
                )
        newer = WorkflowRevision.objects.create(
            workflow=workflow,
            number=3,
            state=WorkflowRevision.STATE_PUBLISHED,
            source=WorkflowRevision.SOURCE_BUILDER,
            definition=_definition("rev-flow"),
            yaml="key: rev-flow\n",
            content_hash="d" * 64,
        )
        published.state = WorkflowRevision.STATE_SUPERSEDED
        published.save(update_fields=["state"])
        workflow.published_revision = newer
        workflow.save(update_fields=["published_revision"])
        draft = workflow.draft_revision
        self.assertTrue(draft.is_outdated)

    def test_when_column_stores_more_than_200_characters(self):
        workflow = Workflow.objects.create(
            key="when-flow", name="When", raw_definition={}, managed_by=MANAGED_BY_NAUTOBOT
        )
        action = WorkflowAction.objects.create(
            workflow=workflow,
            phase="main",
            order=1,
            action_type="awx",
            name="run",
            when="{{ inputs.confirm }}" + (" " * 220),
            awx_template_id=1,
        )
        action.refresh_from_db()
        self.assertGreater(len(action.when), 200)

    def test_backfill_creates_r1_without_rewriting_raw_definition(self):
        raw = {"key": "imported", "name": "Imported", "main_actions": [{"type": "awx"}]}
        workflow = Workflow.objects.create(
            key="imported", name="Imported", raw_definition=raw, managed_by=MANAGED_BY_GIT
        )
        workflow.revisions.all().delete()
        workflow.published_revision = None
        workflow.save(update_fields=["published_revision"])
        backfill = importlib.import_module("nautobot_workflow_engine.migrations.0008_backfill_revisions")
        backfill.backfill_revisions(apps, None)
        workflow.refresh_from_db()
        self.assertEqual(workflow.raw_definition, raw)
        revision = workflow.published_revision
        self.assertEqual(revision.number, 1)
        self.assertEqual(revision.state, WorkflowRevision.STATE_PUBLISHED)
        self.assertEqual(revision.source, WorkflowRevision.SOURCE_GIT_IMPORT)
        self.assertEqual(revision.definition, raw)

    def test_launch_form_reads_multiple_dependencies_and_jinja_show_if(self):
        from nautobot_workflow_engine.datasources import save_workflow_definition
        from nautobot_workflow_engine.forms import DynamicWorkflowForm

        definition = _definition("deps-flow")
        definition["inputs"] = [
            {"key": "site", "label": "Site", "type": "string"},
            {"key": "role", "label": "Role", "type": "string"},
            {
                "key": "target",
                "label": "Target",
                "type": "string",
                "depends_on": ["site", "role"],
                "show_if": "{{ inputs.site }}",
            },
        ]
        workflow = save_workflow_definition(definition, source_repo=None)
        form = DynamicWorkflowForm(workflow)
        self.assertEqual(form.fields["target"].widget.attrs["data-depends-on"], "site,role")
        self.assertEqual(form.fields["target"].widget.attrs["data-show-if-template"], "{{ inputs.site }}")
        self.assertEqual(list(workflow.inputs.get(key="target").depends_on), ["site", "role"])
        shown = DynamicWorkflowForm(
            data={"site": "lab", "role": "edge", "target": "sw1", "execution_type": "immediate"},
            workflow=workflow,
        )
        self.assertTrue(shown.is_valid(), shown.errors)
        self.assertEqual(shown.cleaned_data["target"], "sw1")
        hidden = DynamicWorkflowForm(
            data={"site": "", "role": "edge", "target": "sw1", "execution_type": "immediate"},
            workflow=workflow,
        )
        self.assertTrue(hidden.is_valid(), hidden.errors)
        self.assertIsNone(hidden.cleaned_data["target"])

    def test_launch_form_enforces_case_insensitive_patterns(self):
        from nautobot_workflow_engine.datasources import save_workflow_definition
        from nautobot_workflow_engine.forms import DynamicWorkflowForm

        definition = _definition("pattern-flow")
        definition["inputs"] = [
            {
                "key": "reason",
                "label": "Reason",
                "type": "string",
                "validators": [
                    {
                        "regex": r"^[a-z0-9 .,']{3,}$",
                        "flags": ["IGNORECASE"],
                        "message": "Use at least 3 letters or numbers.",
                    }
                ],
            }
        ]
        workflow = save_workflow_definition(definition, source_repo=None)
        rejected = DynamicWorkflowForm(
            data={"reason": "!!", "execution_type": "immediate"},
            workflow=workflow,
        )
        self.assertFalse(rejected.is_valid())
        self.assertIn("Use at least 3 letters or numbers.", rejected.errors["reason"])
        accepted = DynamicWorkflowForm(
            data={"reason": "Power Supply", "execution_type": "immediate"},
            workflow=workflow,
        )
        self.assertTrue(accepted.is_valid(), accepted.errors)
