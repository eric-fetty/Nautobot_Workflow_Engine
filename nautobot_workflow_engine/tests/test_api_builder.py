"""Builder API permissions and error bodies."""

import json
import warnings

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from nautobot.extras.models import GitRepository
from nautobot.users.models import ObjectPermission
from rest_framework.test import APIClient

from nautobot_workflow_engine.api.urls import urlpatterns
from nautobot_workflow_engine.models import MANAGED_BY_GIT, MANAGED_BY_NAUTOBOT, Workflow, WorkflowRevision

User = get_user_model()
_PERM_SEQ = 0


def grant(user, actions, model=Workflow, constraints=None):
    """Grant one object permission."""
    global _PERM_SEQ
    _PERM_SEQ += 1
    permission = ObjectPermission.objects.create(
        name=f"api-{_PERM_SEQ}-{user.username}-{'-'.join(actions)}",
        actions=actions,
        constraints=constraints,
        enabled=True,
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


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


class BuilderApiTestCase(TestCase):
    """Every M2 endpoint, including 400, 403, 404, and 409."""

    def setUp(self):
        self.user = User.objects.create(username="api-editor")
        self.other = User.objects.create(username="api-other")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _url(self, name, **kwargs):
        return reverse(f"plugins-api:nautobot_workflow_engine-api:{name}", kwargs=kwargs or None)

    def _send(self, method, name, body=None, **kwargs):
        url = self._url(name, **kwargs)
        if body is None:
            return getattr(self.client, method)(url)
        return getattr(self.client, method)(url, data=json.dumps(body), content_type="application/json")

    def test_anonymous_and_unprivileged_list_are_forbidden(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get(self._url("workflow-list")).status_code, 403)
        self.assertEqual(self._send("get", "workflow-list").status_code, 403)
        grant(self.user, ["view"])
        # The first request caches an empty object-permission set on this user.
        if hasattr(self.user, "_object_perm_cache"):
            del self.user._object_perm_cache
        self.assertEqual(self._send("get", "workflow-list").status_code, 200)

    def test_schema_etag_and_context(self):
        grant(self.user, ["view", "add"])
        first = self._send("get", "builder-schema")
        self.assertEqual(first.status_code, 200)
        self.assertIn("depends_on", json.dumps(first.data))
        etag = first["ETag"]
        cached = self.client.get(self._url("builder-schema"), HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(cached.status_code, 304)
        context = self._send("get", "builder-context")
        self.assertEqual(context.status_code, 200)
        self.assertIn("capabilities", context.data)
        self.assertTrue(context.data["capabilities"]["can_add"])

    def test_validate_never_500s_and_reports_jinja(self):
        grant(self.user, ["add"])
        bad = self.client.post(self._url("builder-validate"), data=b"\xff", content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data["errors"][0]["pointer"], "")
        broken = self.client.post(
            self._url("builder-validate"),
            data=b"{",
            content_type="application/json",
        )
        self.assertEqual(broken.status_code, 400)
        self.assertEqual(broken.data["errors"][0]["pointer"], "")
        checked = self._send(
            "post",
            "builder-validate",
            {
                "definition": _definition(
                    actions=[
                        {"type": "awx", "name": "run", "template_id": 1, "when": "{{ cycler.__init__.__globals__ }}"}
                    ]
                )
            },
        )
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(any(item["code"] == "template_rejected" for item in checked.data["errors"]))
        self.assertIn("username", checked.data["references"]["user"]["attributes"])

    def test_refactor_renames_dependencies(self):
        grant(self.user, ["change"])
        response = self._send(
            "post",
            "builder-refactor",
            {
                "definition": _definition(
                    inputs=[
                        {"key": "site", "type": "string"},
                        {"key": "target", "type": "string", "depends_on": ["site"]},
                    ]
                ),
                "op": "rename_input",
                "from": "site",
                "to": "location",
            },
        )
        self.assertEqual(response.status_code, 200)
        target = response.data["definition"]["inputs"][1]
        self.assertEqual(target["depends_on"], ["location"])

    def test_draft_does_not_change_runtime_and_stale_edit_names_the_editor(self):
        grant(self.user, ["view", "add", "change", "author_code"])
        grant(self.other, ["view", "change", "author_code"])
        created = self._send("post", "workflow-list", {"definition": _definition()})
        self.assertEqual(created.status_code, 201)
        workflow = Workflow.objects.get(key="lab-flow")
        self.assertEqual(workflow.raw_definition, {})
        self.assertFalse(workflow.inputs.exists())
        published = self._send(
            "post",
            "workflow-publish",
            {"definition": _definition(), "base_revision": 1, "enable": False},
            key="lab-flow",
        )
        self.assertEqual(published.status_code, 202)
        workflow.refresh_from_db()
        self.assertEqual(workflow.inputs.get().label, "Hostname")
        for number in (2, 3):
            WorkflowRevision.objects.filter(workflow=workflow, state=WorkflowRevision.STATE_PUBLISHED).update(
                state=WorkflowRevision.STATE_SUPERSEDED
            )
            revision = workflow.revisions.order_by("-number").first()
            newer = WorkflowRevision.objects.create(
                workflow=workflow,
                number=number,
                state=WorkflowRevision.STATE_PUBLISHED,
                source=WorkflowRevision.SOURCE_API,
                definition=_definition(),
                yaml="key: lab-flow\n",
                content_hash=f"{number:064d}",
                created_by=self.user,
                base_revision=revision,
            )
            workflow.published_revision = newer
            workflow.save(update_fields=["published_revision"])
        self.assertEqual(workflow.published_revision.number, 3)
        changed = _definition()
        changed["inputs"] = [{"key": "hostname", "label": "Device", "type": "string"}]
        first = self._send("put", "workflow-draft", {"definition": changed, "base_revision": 3}, key="lab-flow")
        self.assertEqual(first.status_code, 200)
        workflow.refresh_from_db()
        self.assertEqual(workflow.raw_definition["inputs"][0]["label"], "Hostname")
        other = APIClient()
        other.force_authenticate(self.other)
        second = other.put(
            self._url("workflow-draft", key="lab-flow"),
            data=json.dumps({"definition": changed, "base_revision": 3}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["code"], "stale_revision")
        self.assertIn("Updated in Nautobot by api-editor", second.data["detail"])
        self.assertIn("reload to continue", second.data["detail"])
        self.assertEqual(second.data["author"], "api-editor")

    def test_missing_and_out_of_scope_are_404(self):
        grant(self.user, ["view", "change"], constraints={"category": "lab"})
        Workflow.objects.create(
            key="prod-flow",
            name="Prod",
            category="prod",
            raw_definition=_definition("prod-flow"),
            managed_by=MANAGED_BY_NAUTOBOT,
        )
        missing = self._send("get", "workflow-detail", key="missing")
        hidden = self._send("get", "workflow-detail", key="prod-flow")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(hidden.status_code, 404)
        self.assertIn("detail", hidden.data)

    def test_git_managed_draft_saves(self):
        grant(self.user, ["view", "change"])
        repo = GitRepository.objects.create(name="Git", remote_url="https://example.com/git.git")
        Workflow.objects.create(
            key="git-flow",
            name="Git",
            raw_definition=_definition("git-flow"),
            managed_by=MANAGED_BY_GIT,
            source_repo=repo,
        )
        response = self._send(
            "put",
            "workflow-draft",
            {"definition": _definition("git-flow"), "base_revision": None, "strict": False},
            key="git-flow",
        )
        self.assertEqual(response.status_code, 200, getattr(response, "content", b""))
        self.assertNotIn("Managed in Git", str(response.data.get("detail", "")))
        self.assertEqual(Workflow.objects.get(key="git-flow").draft_revision.state, "draft")

    def test_revisions_diff_restore_duplicate_and_enable(self):
        grant(self.user, ["view", "add", "change", "publish", "author_code"])
        created = self._send("post", "workflow-list", {"definition": _definition("copy-source")})
        self.assertEqual(created.status_code, 201)
        published = self._send(
            "post",
            "workflow-publish",
            {"definition": _definition("copy-source"), "base_revision": 1},
            key="copy-source",
        )
        self.assertEqual(published.status_code, 202)
        listed = self._send("get", "workflow-revisions", key="copy-source")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data[0]["number"], 1)
        diff = self.client.get(self._url("workflow-revision-diff", key="copy-source", number=1) + "?against=published")
        self.assertEqual(diff.status_code, 200)
        self.assertIn("original_yaml", diff.data)
        self.assertIn("modified_yaml", diff.data)
        restored = self._send("post", "workflow-revision-restore", {}, key="copy-source", number=1)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.data["revision"]["state"], "draft")
        copied = self._send(
            "post", "workflow-duplicate", {"key": "copy-source-copy", "name": "Copy"}, key="copy-source"
        )
        self.assertEqual(copied.status_code, 201)
        duplicate = Workflow.objects.get(key="copy-source-copy")
        self.assertEqual(duplicate.managed_by, MANAGED_BY_NAUTOBOT)
        self.assertFalse(duplicate.enabled)
        self.assertEqual(duplicate.raw_definition, {})
        enabled = self._send("post", "workflow-enable", key="copy-source")
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(Workflow.objects.get(key="copy-source").enabled)
        disabled = self._send("post", "workflow-disable", key="copy-source")
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(Workflow.objects.get(key="copy-source").enabled)
        self._send("delete", "workflow-draft", key="copy-source")
        self.assertIsNone(Workflow.objects.get(key="copy-source").draft_revision)

    def test_openapi_schema_has_no_warnings(self):
        generator = SchemaGenerator(patterns=urlpatterns)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            schema = generator.get_schema(request=None, public=True)
        spectacular = [item for item in caught if "spectacular" in item.category.__module__]
        self.assertEqual(spectacular, [], msg=[str(item.message) for item in spectacular])
        self.assertIn("paths", schema)
