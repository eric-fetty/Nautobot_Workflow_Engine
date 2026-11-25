"""Nautobot tags on workflows: model, API, builder, list filter, bulk edit, Git sync and YAML."""

import importlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from nautobot.core.jobs import BulkEditObjects
from nautobot.core.testing import TransactionTestCase, run_job_for_testing
from nautobot.extras.models import GitRepository, Job, ObjectChange, Tag, TaggedItem
from nautobot.users.models import ObjectPermission
from rest_framework.test import APIClient

from nautobot_workflow_engine.datasources import sync_workflows_from_repo
from nautobot_workflow_engine.filters import WorkflowFilterSet
from nautobot_workflow_engine.forms import WorkflowBulkEditForm
from nautobot_workflow_engine.models import MANAGED_BY_GIT, MANAGED_BY_NAUTOBOT, Workflow
from nautobot_workflow_engine.schema.builder_payload import builder_from_definition, definition_from_builder
from nautobot_workflow_engine.schema.yaml_io import to_yaml

User = get_user_model()
_PERM_SEQ = 0


def grant(user, actions, model=Workflow, constraints=None):
    """Grant one object permission."""
    global _PERM_SEQ
    _PERM_SEQ += 1
    permission = ObjectPermission.objects.create(
        name=f"tags-{_PERM_SEQ}-{user.username}-{'-'.join(actions)}",
        actions=actions,
        constraints=constraints,
        enabled=True,
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


def make_tag(name, color="2196f3", workflows=True):
    """A Nautobot tag, allowed on workflows unless ``workflows`` is False."""
    tag = Tag.objects.create(name=name, color=color)
    if workflows:
        tag.content_types.add(ContentType.objects.get_for_model(Workflow))
    return tag


def _definition(key="lab-flow", **overrides):
    data = {
        "key": key,
        "name": "Lab flow",
        "category": "lab",
        "inputs": [{"key": "hostname", "label": "Hostname", "type": "string"}],
        "actions": [{"type": "awx", "name": "run", "template_id": 3}],
    }
    data.update(overrides)
    return data


def _workflow(key, category="lab", **extra):
    extra.setdefault("raw_definition", _definition(key, category=category))
    return Workflow.objects.create(key=key, name=key.title(), category=category, **extra)


class WorkflowTagModelTestCase(TestCase):
    """Workflow.tags is Nautobot's TagsField."""

    def test_tags_are_nautobot_tags(self):
        workflow = _workflow("model-flow")
        tag = make_tag("Change Freeze Scope")
        workflow.tags.add(tag)
        self.assertEqual(list(workflow.tags.all()), [tag])
        item = TaggedItem.objects.get(object_id=workflow.pk)
        self.assertEqual(item.content_type, ContentType.objects.get_for_model(Workflow))
        self.assertIn(workflow, Workflow.objects.filter(tags=tag))

    def test_workflow_is_offered_as_a_tag_content_type(self):
        allowed = make_tag("Allowed")
        make_tag("Devices only", workflows=False)
        self.assertEqual(list(Tag.objects.get_for_model(Workflow)), [allowed])
        from nautobot.extras.utils import TaggableClassesQuery

        labels = {label for label, _pk in TaggableClassesQuery().get_choices()}
        self.assertIn("nautobot_workflow_engine.workflow", labels)


class WorkflowTagMigrationTestCase(TestCase):
    """0017 keeps old taggit rows and only reports them."""

    def test_legacy_taggit_rows_are_reported_and_kept(self):
        from django.apps import apps
        from taggit.models import Tag as TaggitTag
        from taggit.models import TaggedItem as TaggitItem

        migration = importlib.import_module("nautobot_workflow_engine.migrations.0017_workflow_nautobot_tags")
        content_type = ContentType.objects.get_for_model(Workflow)
        legacy = TaggitTag.objects.create(name="legacy", slug="legacy")
        TaggitItem.objects.create(tag=legacy, content_type=content_type, object_id=1)
        with self.assertLogs(migration.logger, level="WARNING") as logs:
            migration.report_legacy_taggit_rows(apps, None)
        self.assertIn("1 django-taggit row", logs.output[0])
        self.assertEqual(TaggitItem.objects.filter(content_type=content_type).count(), 1)
        self.assertFalse(TaggedItem.objects.filter(content_type=content_type).exists())

    def test_operations_swap_the_field_without_deleting_rows(self):
        migration = importlib.import_module("nautobot_workflow_engine.migrations.0017_workflow_nautobot_tags").Migration
        names = [type(operation).__name__ for operation in migration.operations]
        self.assertEqual(names, ["RunPython", "RemoveField", "AddField", "AlterField"])
        self.assertEqual(migration.operations[2].field.remote_field.through, "extras.TaggedItem")


class WorkflowTagApiTestCase(TestCase):
    """Builder API reads and writes tags; tags never reach the YAML."""

    def setUp(self):
        self.user = User.objects.create(username="tag-editor")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        grant(self.user, ["view", "add", "change"])
        grant(self.user, ["view"], model=Tag)
        self.prod = make_tag("Production", color="f44336")
        self.lab = make_tag("Lab", color="ffeb3b")

    def _url(self, name, **kwargs):
        return reverse(f"plugins-api:nautobot_workflow_engine-api:{name}", kwargs=kwargs or None)

    def _send(self, method, name, body, **kwargs):
        return getattr(self.client, method)(
            self._url(name, **kwargs), data=json.dumps(body), content_type="application/json"
        )

    def test_create_with_names_and_ids_and_read_back(self):
        response = self._send(
            "post", "workflow-list", {"definition": _definition(), "tags": ["Production", str(self.lab.pk)]}
        )
        self.assertEqual(response.status_code, 201, response.content)
        workflow = Workflow.objects.get(key="lab-flow")
        self.assertEqual(set(workflow.tags.values_list("name", flat=True)), {"Production", "Lab"})
        names = {row["name"]: row["color"] for row in response.data["workflow"]["tags"]}
        self.assertEqual(names, {"Lab": "ffeb3b", "Production": "f44336"})
        detail = self.client.get(self._url("workflow-detail", key="lab-flow"))
        self.assertEqual(len(detail.data["workflow"]["tags"]), 2)
        listing = self.client.get(self._url("workflow-list"))
        self.assertEqual(len(listing.data[0]["tags"]), 2)

    def test_unknown_or_disallowed_tag_is_400_and_saves_nothing(self):
        make_tag("Devices only", workflows=False)
        for bad in (["Nope"], ["Devices only"], "Production"):
            response = self._send("post", "workflow-list", {"definition": _definition(), "tags": bad})
            self.assertEqual(response.status_code, 400, bad)
            self.assertEqual(response.data["errors"][0]["pointer"], "/tags")
        self.assertFalse(Workflow.objects.filter(key="lab-flow").exists())

    def test_draft_save_replaces_tags_from_the_builder_payload(self):
        self._send("post", "workflow-list", {"definition": _definition(), "tags": ["Production"]})
        workflow = Workflow.objects.get(key="lab-flow")
        payload = builder_from_definition(_definition())
        payload["tags"] = ["Lab"]
        response = self._send("put", "workflow-draft", {"payload": payload, "base_revision": 1}, key="lab-flow")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(workflow.tags.values_list("name", flat=True)), ["Lab"])
        # A body without tags leaves them alone.
        head = workflow.draft_revision.number
        response = self._send(
            "put", "workflow-draft", {"definition": _definition(), "base_revision": head}, key="lab-flow"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(workflow.tags.values_list("name", flat=True)), ["Lab"])
        # A rejected tag leaves the draft and the tags as they were.
        head = workflow.draft_revision.number
        response = self._send(
            "put",
            "workflow-draft",
            {"definition": _definition(name="Renamed"), "base_revision": head, "tags": ["x"]},
            key="lab-flow",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(workflow.draft_revision.definition["name"], "Lab flow")
        self.assertEqual(list(workflow.tags.values_list("name", flat=True)), ["Lab"])

    def test_publish_sets_tags_and_yaml_has_none(self):
        self._send("post", "workflow-list", {"definition": _definition()})
        response = self._send(
            "post",
            "workflow-publish",
            {"definition": _definition(), "base_revision": 1, "enable": False, "tags": ["Production"]},
            key="lab-flow",
        )
        self.assertEqual(response.status_code, 202, response.content)
        workflow = Workflow.objects.get(key="lab-flow")
        self.assertEqual(list(workflow.tags.values_list("name", flat=True)), ["Production"])
        self.assertNotIn("tags", workflow.raw_definition)
        self.assertNotIn("tags", workflow.published_revision.definition)
        self.assertNotIn("tags:", workflow.published_revision.yaml)

    def test_tags_the_user_cannot_see_are_kept(self):
        secret = make_tag("Secret")
        restricted = User.objects.create(username="tag-limited")
        grant(restricted, ["view", "add", "change"])
        grant(restricted, ["view"], model=Tag, constraints={"name__in": ["Production", "Lab"]})
        workflow = _workflow("lab-flow")
        workflow.tags.add(secret, self.prod)
        client = APIClient()
        client.force_authenticate(restricted)
        from nautobot_workflow_engine.api.views import _apply_tags, _resolve_tags

        tags = _resolve_tags(workflow, restricted, {"tags": ["Lab"]})
        _apply_tags(workflow, restricted, tags)
        self.assertEqual(set(workflow.tags.values_list("name", flat=True)), {"Lab", "Secret"})
        with self.assertRaises(Exception):
            _resolve_tags(workflow, restricted, {"tags": ["Secret"]})

    def test_tag_already_on_workflow_stays_valid_after_content_type_change(self):
        workflow = _workflow("lab-flow")
        workflow.tags.add(self.prod)
        self.prod.content_types.clear()
        from nautobot_workflow_engine.api.views import _resolve_tags

        self.assertEqual(_resolve_tags(workflow, self.user, {"tags": ["Production"]}), [self.prod])

    def test_yaml_round_trip_never_writes_tags(self):
        payload = builder_from_definition(_definition())
        payload["tags"] = ["Production"]
        definition = definition_from_builder(payload)
        self.assertNotIn("tags", definition)
        self.assertNotIn("tags", to_yaml(definition))


class WorkflowTagBuilderConfigTestCase(TestCase):
    """The builder page offers only tags allowed on workflows and loads the workflow's tags."""

    def test_tag_choices_and_payload(self):
        user = User.objects.create(username="tag-builder", is_superuser=True)
        allowed = make_tag("Allowed", color="4caf50")
        make_tag("Devices only", workflows=False)
        workflow = _workflow("cfg-flow")
        workflow.tags.add(allowed)
        from nautobot_workflow_engine.views import _builder_config

        request = SimpleNamespace(user=user, META={}, COOKIES={})
        with patch("nautobot_workflow_engine.views.get_token", return_value="t"):
            config = _builder_config(request, {"tags": []}, workflow=workflow, is_edit=True)
        self.assertEqual(config["tag_choices"], [{"id": str(allowed.pk), "name": "Allowed", "color": "4caf50"}])
        self.assertEqual(config["payload"]["tags"], ["Allowed"])
        self.assertTrue(config["tag_add_url"])


class WorkflowTagListTestCase(TestCase):
    """Workflows list: tag filter, chips and the bulk edit controls."""

    def setUp(self):
        self.tag = make_tag("Networking", color="9c27b0")
        self.tagged = _workflow("tagged-flow", enabled=True)
        self.plain = _workflow("plain-flow", enabled=True)
        self.tagged.tags.add(self.tag)

    def test_filterset_filters_by_tag_name_and_id(self):
        self.assertEqual(list(WorkflowFilterSet({"tags": ["Networking"]}, Workflow.objects.all()).qs), [self.tagged])
        self.assertEqual(
            list(WorkflowFilterSet({"tags": [str(self.tag.pk)]}, Workflow.objects.all()).qs), [self.tagged]
        )

    def test_list_filters_and_shows_chips_and_bulk_controls(self):
        editor = User.objects.create(username="tag-list-editor")
        grant(editor, ["view", "change"])
        self.client.force_login(editor)
        url = reverse("plugins:nautobot_workflow_engine:workflows_list")
        response = self.client.get(url, {"tags": "Networking"})
        self.assertEqual(response.status_code, 200)
        names = [workflow.key for workflow in response.context["catalog"]]
        self.assertEqual(names, ["tagged-flow"])
        body = response.content.decode()
        self.assertIn("?tags=Networking", body)
        self.assertIn('id="nwe-bulk-form"', body)
        self.assertIn(reverse("plugins:nautobot_workflow_engine:workflow_bulk_edit"), body)
        self.assertIn(f'value="{self.tagged.pk}"', body)
        missing = self.client.get(url, {"tags": "no-such-tag"})
        self.assertEqual(list(missing.context["catalog"]), [])

    def test_viewer_gets_no_bulk_controls(self):
        viewer = User.objects.create(username="tag-list-viewer")
        grant(viewer, ["view"])
        self.client.force_login(viewer)
        response = self.client.get(reverse("plugins:nautobot_workflow_engine:workflows_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="nwe-bulk-form"', response.content.decode())


class WorkflowBulkEditTagsTestCase(TransactionTestCase):
    """Bulk edit adds and removes tags through Nautobot's Bulk Edit job, within the change constraint.

    A TransactionTestCase, like Nautobot's own Job tests, so the job can write its logs.
    """

    def setUp(self):
        super().setUp()
        from nautobot.extras.utils import refresh_job_model_from_job_class

        # TransactionTestCase truncates tables between tests, Job rows included.
        refresh_job_model_from_job_class(Job, BulkEditObjects)
        self.user = User.objects.create(username="tag-bulk")
        grant(self.user, ["view"])
        grant(self.user, ["change"], constraints={"category": "lab"})
        grant(self.user, ["view"], model=Tag)
        self.add = make_tag("Add me")
        self.remove = make_tag("Remove me")
        self.lab_one = _workflow("lab-one")
        # Never published: raw_definition is empty, as the builder leaves it for a new draft.
        self.lab_new = _workflow("lab-new", raw_definition={})
        self.prod = _workflow("prod-one", category="prod")
        for workflow in (self.lab_one, self.lab_new, self.prod):
            workflow.tags.add(self.remove)

    def _run(self, pks, form_data):
        job_model = Job.objects.get_for_class_path(BulkEditObjects.class_path)
        job_form = BulkEditObjects.as_form(
            data={
                "content_type": ContentType.objects.get_for_model(Workflow).pk,
                "form_data": {"pk": [str(pk) for pk in pks], "_nullify": [], **form_data},
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

    def test_form_has_only_tag_fields(self):
        form = WorkflowBulkEditForm(Workflow)
        self.assertEqual(set(form.fields), {"pk", "add_tags", "remove_tags"})

    def test_bulk_edit_page_lists_only_editable_workflows(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("plugins:nautobot_workflow_engine:workflow_bulk_edit"),
            {"pk": [self.lab_one.pk, self.prod.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["objs_count"], 1)
        body = response.content.decode()
        self.assertIn("add_tags", body)
        self.assertNotIn('name="category"', body)

    def test_add_and_remove_tags_with_change_log(self):
        result = self._run(
            [self.lab_one.pk, self.lab_new.pk],
            {"add_tags": [str(self.add.pk)], "remove_tags": [str(self.remove.pk)]},
        )
        self.assertEqual(result.status, "SUCCESS", result.result)
        for workflow in (self.lab_one, self.lab_new):
            self.assertEqual(list(workflow.tags.values_list("name", flat=True)), ["Add me"])
        self.assertTrue(
            ObjectChange.objects.filter(
                changed_object_type=ContentType.objects.get_for_model(Workflow), changed_object_id=self.lab_one.pk
            ).exists()
        )
        self.lab_new.refresh_from_db()
        self.assertEqual(self.lab_new.raw_definition, {})

    def test_workflows_outside_the_change_constraint_are_skipped(self):
        self._run([self.lab_one.pk, self.prod.pk], {"add_tags": [str(self.add.pk)]})
        self.assertIn("Add me", self.lab_one.tags.values_list("name", flat=True))
        self.assertNotIn("Add me", self.prod.tags.values_list("name", flat=True))


class WorkflowTagGitSyncTestCase(TestCase):
    """Git sync never removes tags: they are Nautobot metadata, not YAML."""

    def setUp(self):
        self.repo = GitRepository.objects.create(name="Tag Repo", remote_url="https://example.com/tags.git")
        self.job_result = SimpleNamespace(log=lambda **kwargs: None)
        self.tag = make_tag("Keep me")

    def _sync(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            workflow_dir = Path(tmp) / "workflows"
            workflow_dir.mkdir()
            for name, text in files.items():
                (workflow_dir / name).write_text(text, encoding="utf-8")
            with patch.object(GitRepository, "filesystem_path", property(lambda _self: tmp)):
                sync_workflows_from_repo(self.repo, self.job_result, delete=False)

    def test_git_managed_workflow_keeps_tags_after_sync(self):
        workflow = Workflow.objects.create(
            key="git-flow",
            name="Before",
            enabled=True,
            managed_by=MANAGED_BY_GIT,
            source_repo=self.repo,
            raw_definition={"key": "git-flow", "name": "Before", "enabled": True},
        )
        workflow.tags.add(self.tag)
        self._sync({"git-flow.yaml": "key: git-flow\nname: After\nenabled: true\n"})
        workflow.refresh_from_db()
        self.assertEqual(workflow.name, "After")
        self.assertEqual(list(workflow.tags.values_list("name", flat=True)), ["Keep me"])

    def test_nautobot_managed_workflow_keeps_tags_after_sync(self):
        workflow = Workflow.objects.create(
            key="ui-flow",
            name="From the builder",
            enabled=True,
            managed_by=MANAGED_BY_NAUTOBOT,
            source_repo=self.repo,
            git_status="in_sync",
            git_content_hash="0" * 64,
            raw_definition={"key": "ui-flow", "name": "From the builder", "enabled": True},
        )
        workflow.tags.add(self.tag)
        self._sync({"ui-flow.yaml": "key: ui-flow\nname: From Git\nenabled: true\n"})
        workflow.refresh_from_db()
        self.assertEqual(workflow.name, "From Git")
        self.assertEqual(list(workflow.tags.values_list("name", flat=True)), ["Keep me"])
