"""Change-window timezone: picker choices and validation."""

import datetime as dt
import unittest

import zoneinfo
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from nautobot_workflow_engine.schema.builder_payload import definition_from_builder
from nautobot_workflow_engine.schema.errors import BuilderValidationError
from nautobot_workflow_engine.tests.test_builders import _base
from nautobot_workflow_engine.timezones import COMMON_TIMEZONES, is_valid_timezone, timezone_choices


def _with_timezone(name):
    return _base(
        inputs=[{"key": "change_ticket", "type": "string", "required": True}],
        schedule={
            "enabled": True,
            "integration_name": "ServiceNow Lab",
            "change_input_key": "change_ticket",
            "timezone": name,
        },
    )


class TimezoneChoicesTestCase(unittest.TestCase):
    def test_common_zones_come_first_then_every_zone_alphabetically(self):
        names = [row["name"] for row in timezone_choices()]
        self.assertEqual(names[0], "UTC")
        self.assertEqual(names[: len(COMMON_TIMEZONES)], list(COMMON_TIMEZONES))
        rest = names[len(COMMON_TIMEZONES) :]
        self.assertEqual(rest, sorted(rest))
        self.assertEqual(set(names), set(zoneinfo.available_timezones()) | {"UTC"})
        self.assertEqual(len(names), len(set(names)))

    def test_rows_carry_an_offset_hint(self):
        now = dt.datetime(2026, 1, 15, 12, tzinfo=dt.timezone.utc)
        rows = {row["name"]: row for row in timezone_choices(now)}
        self.assertEqual(rows["UTC"]["offset"], "UTC+00:00")
        self.assertEqual(rows["America/New_York"]["offset"], "UTC−05:00")
        self.assertEqual(rows["Asia/Kolkata"]["offset"], "UTC+05:30")
        self.assertTrue(rows["Europe/Amsterdam"]["common"])
        self.assertFalse(rows["Africa/Abidjan"]["common"])

    def test_is_valid_timezone(self):
        for name in ("", "UTC", "utc", "Europe/Amsterdam", "America/Argentina/Buenos_Aires"):
            self.assertTrue(is_valid_timezone(name), name)
        for name in ("Not/AZone", "Europe/amsterdam", "../etc/passwd", None, 5):
            self.assertFalse(is_valid_timezone(name), name)


class TimezoneValidationTestCase(unittest.TestCase):
    def test_a_listed_zone_round_trips_into_the_definition(self):
        definition = definition_from_builder(_with_timezone("Europe/Amsterdam"))
        self.assertEqual(definition["schedule"]["servicenow"]["timezone"], "Europe/Amsterdam")

    def test_empty_means_utc_and_is_allowed(self):
        definition = definition_from_builder(_with_timezone(""))
        self.assertIn(definition["schedule"]["servicenow"].get("timezone", ""), ("", "UTC"))

    def test_an_unknown_zone_is_rejected_on_the_schedule_field(self):
        with self.assertRaises(BuilderValidationError) as raised:
            definition_from_builder(_with_timezone("Mars/Olympus_Mons"))
        errors = [item for item in raised.exception.errors if item["path"] == "schedule.timezone"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Choose a timezone from the list", errors[0]["message"])


class TimezoneBuilderConfigTestCase(TestCase):
    def test_builder_page_lists_timezones(self):
        user = get_user_model().objects.create_user(username="tz-admin", is_superuser=True)
        self.client.force_login(user)
        response = self.client.get(reverse("plugins:nautobot_workflow_engine:workflow_builder_create"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('"name": "America/New_York"', body)
        self.assertIn('"common": true', body)

    def test_validate_api_rejects_unknown_zone(self):
        user = get_user_model().objects.create_user(username="tz-api", is_superuser=True)
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(
            reverse("plugins-api:nautobot_workflow_engine-api:builder-validate"),
            data={"payload": _with_timezone("Nowhere/Land")},
            format="json",
        )
        self.assertIn("timezone_invalid", response.content.decode())
        self.assertIn("Choose a timezone from the list", response.content.decode())
