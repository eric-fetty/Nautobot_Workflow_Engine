"""Visual filter fields come from the model's FilterSet."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from nautobot.dcim.models import Device, Location
from rest_framework.test import APIClient

from nautobot_workflow_engine.schema.builder_payload import _name_lookup_fields, _query_from_rows, _rows_from_query
from nautobot_workflow_engine.schema.filter_fields import filter_fields_for_model

User = get_user_model()


class FilterFieldTests(TestCase):
    def test_device_fields_are_typed(self):
        fields = {row["key"]: row for row in filter_fields_for_model(Device)}
        self.assertEqual(fields["name"]["kind"], "text")
        self.assertIn("contains", fields["name"]["operators"])
        self.assertEqual(fields["location"]["kind"], "object")
        self.assertEqual(fields["location"]["related_model"], "dcim.Location")
        self.assertEqual(fields["location"]["lookup"], "name")
        self.assertIsInstance(fields["location"]["examples"], list)
        self.assertEqual(fields["status"]["kind"], "choice")
        self.assertEqual(fields["status"]["lookup"], "name")
        self.assertEqual(fields["role"]["kind"], "choice")
        self.assertEqual(fields["name"]["lookup"], "")
        self.assertIn("greater than", fields["position"]["operators"])
        self.assertNotIn("interfaces", fields)
        self.assertNotIn("name__ic", fields)
        self.assertNotIn("location__n", fields)
        self.assertNotIn("position__gt", fields)

    def test_location_name_is_text(self):
        fields = {row["key"]: row for row in filter_fields_for_model(Location)}
        self.assertEqual(fields["name"]["kind"], "text")
        self.assertEqual(fields["name"]["label"], "Name")

    def test_api_rejects_unknown_models(self):
        user = User.objects.create_superuser(username="filter-fields", password="x")
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(
            "/api/plugins/workflow-engine/builder/filter-fields/",
            {"model": "auth.User"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_api_returns_device_location(self):
        user = User.objects.create_superuser(username="filter-fields-ok", password="x")
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(
            "/api/plugins/workflow-engine/builder/filter-fields/",
            {"model": "dcim.Device"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        keys = [row["key"] for row in response.json()["fields"]]
        self.assertIn("location", keys)
        self.assertIn("status", keys)
        self.assertIn("examples", response.json())

    def test_fixed_related_values_store_by_name(self):
        query, depends = _query_from_rows(
            [
                {"field": "status", "op": "is", "source": "value", "value": "Active"},
                {"field": "role", "op": "is one of", "source": "value", "value": "Access switch, Distribution switch"},
                {"field": "location", "op": "is", "source": "input", "input": "location"},
                {"field": "name", "op": "contains", "source": "value", "value": "lab"},
                {"field": "status", "op": "is not", "source": "value", "value": "Decommissioning"},
            ],
            ["status", "role", "location"],
        )
        self.assertEqual(query["status__name"], "Active")
        self.assertEqual(query["role__name"], ["Access switch", "Distribution switch"])
        self.assertEqual(query["location"], "{{ inputs.location.id }}")
        self.assertEqual(query["name__ic"], "lab")
        self.assertEqual(query["status__name__n"], "Decommissioning")
        self.assertEqual(depends, ["location"])
        rows = {row["field"] + ":" + row["op"]: row for row in _rows_from_query(query)}
        self.assertEqual(rows["status:is"]["value"], "Active")
        self.assertEqual(rows["role:is one of"]["value"], "Access switch, Distribution switch")
        self.assertEqual(rows["location:is"]["source"], "input")
        self.assertEqual(rows["location:is"]["input"], "location")
        self.assertEqual(rows["status:is not"]["value"], "Decommissioning")

    def test_name_lookup_fields_follow_the_filterset(self):
        keys = set(_name_lookup_fields("dcim.Device"))
        self.assertIn("status", keys)
        self.assertIn("location", keys)
        self.assertNotIn("name", keys)
        self.assertEqual(_name_lookup_fields("auth.User"), [])
        self.assertEqual(_name_lookup_fields("not-a-model"), [])
