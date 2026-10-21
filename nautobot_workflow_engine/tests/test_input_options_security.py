"""Input options stay inside the caller's object permissions."""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from nautobot.extras.models import Tag
from nautobot.users.models import ObjectPermission

from nautobot_workflow_engine.models import Workflow, WorkflowInput

User = get_user_model()
_PERM_SEQ = 0


def grant(user, actions, model, constraints=None):
    """Grant one object permission and attach it to ``user``."""
    global _PERM_SEQ
    _PERM_SEQ += 1
    permission = ObjectPermission.objects.create(
        name=f"options-{_PERM_SEQ}-{user.username}",
        actions=actions,
        constraints=constraints,
        enabled=True,
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


class InputOptionsSecurityTestCase(TestCase):
    """Restricted querysets, no API fallback, and lookup rejection."""

    def setUp(self):
        self.user = User.objects.create(username="picker")
        self.workflow = Workflow.objects.create(
            key="pick-tags",
            name="Pick tags",
            enabled=True,
            raw_definition={"key": "pick-tags", "name": "Pick tags", "enabled": True},
        )
        self.workflow_input = WorkflowInput.objects.create(
            workflow=self.workflow,
            key="tag",
            label="Tag",
            input_type="object",
            content_type=ContentType.objects.get_for_model(Tag),
            filter_query={"name__icontains": "lab"},
            order=1,
        )
        self.lab_tag = Tag.objects.create(name="lab-a")
        Tag.objects.create(name="lab-b")
        Tag.objects.create(name="prod-a")
        grant(self.user, ["view"], Workflow)
        self.client.force_login(self.user)
        self.url = reverse(
            "plugins:nautobot_workflow_engine:input_options",
            kwargs={"key": self.workflow.key, "child": "tag"},
        )

    def test_queryset_is_restricted(self):
        grant(self.user, ["view"], Tag, constraints={"name": "lab-a"})
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        values = [item["value"] for item in response.json()["options"]]
        self.assertEqual(values, [str(self.lab_tag.pk)])

    def test_api_error_does_not_fall_back_to_all_objects(self):
        grant(self.user, ["view"], Tag)
        with self.settings():
            from unittest.mock import patch

            import requests

            with patch("requests.get", side_effect=requests.RequestException("down")):
                response = self.client.get(self.url, {"search": "lab"})
        body = response.json()
        self.assertEqual(body["options"], [])
        self.assertIn("error", body)

    def test_lookup_traversal_is_rejected(self):
        grant(self.user, ["view"], Tag)
        self.workflow_input.filter_query = {"user__password__startswith": "a"}
        self.workflow_input.save()
        response = self.client.get(self.url)
        body = response.json()
        self.assertEqual(body["options"], [])
        self.assertIn("not allowed", body["error"])

    def test_denylisted_model_is_refused(self):
        grant(self.user, ["view"], User)
        self.workflow_input.content_type = ContentType.objects.get_for_model(User)
        self.workflow_input.filter_query = {}
        self.workflow_input.save()
        response = self.client.get(self.url)
        body = response.json()
        self.assertEqual(body["options"], [])
        self.assertIn("not available", body["error"])
