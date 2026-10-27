"""The Settings page (Installed Apps gear) and how settings are read."""

from constance import config as constance_config
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from nautobot.extras.models import ScheduledJob

from nautobot_workflow_engine import executors, script_files
from nautobot_workflow_engine.forms import WorkflowEngineSettingsForm
from nautobot_workflow_engine.schema.policy import content_type_allowed
from nautobot_workflow_engine.settings_helpers import (
    APP_NAME,
    DEFAULTS,
    SETTING_KEYS,
    constance_key,
    get_setting,
)
from nautobot_workflow_engine.signals import GIT_SCHEDULE_NAME, ensure_git_maintenance_schedule
from nautobot_workflow_engine.utilities.git import plugin_setting

User = get_user_model()
URL_NAME = "plugins:nautobot_workflow_engine:config"


def _plugins(**values):
    merged = dict(settings.PLUGINS_CONFIG)
    merged[APP_NAME] = values
    return merged


def _post_data(**overrides):
    data = WorkflowEngineSettingsForm.initial_from_settings()
    data["show_disabled"] = "on" if data["show_disabled"] else ""
    data.update(overrides)
    return data


class _ResetSettings:
    """Constance caches values outside the test transaction, so put every setting back afterwards."""

    def tearDown(self):
        for key in SETTING_KEYS:
            setattr(constance_config, constance_key(key), DEFAULTS[key])
        super().tearDown()


@override_settings(PLUGINS_CONFIG=_plugins())
class SettingsReadTestCase(_ResetSettings, TestCase):
    """Every setting comes from nautobot_config.py first, then the Settings page."""

    def test_every_runtime_setting_is_on_the_page(self):
        app = django_apps.get_app_config(APP_NAME)
        self.assertEqual(set(app.constance_config), set(SETTING_KEYS))
        for key in (
            "workflow_dir",
            "filters_dir",
            "scripts_dir",
            "builder_model_allowlist",
            "git_committer_name",
            "git_sync_interval_minutes",
            "git_push_retry_backoff_minutes",
            "awx_external_integration_name",
        ):
            self.assertIn(key, SETTING_KEYS)
        self.assertEqual(app.config_view_name, URL_NAME)

    def test_defaults_come_from_the_page_until_changed(self):
        self.assertEqual(get_setting("scripts_dir"), DEFAULTS["scripts_dir"])
        setattr(constance_config, constance_key("scripts_dir"), "automation/scripts")
        self.assertEqual(get_setting("scripts_dir"), "automation/scripts")
        self.assertEqual(script_files.scripts_dir(), "automation/scripts")
        self.assertEqual(plugin_setting("scripts_dir"), "automation/scripts")

    def test_nautobot_config_wins(self):
        setattr(constance_config, constance_key("workflow_dir"), "from-page")
        with override_settings(PLUGINS_CONFIG=_plugins(workflow_dir="from-file")):
            self.assertEqual(get_setting("workflow_dir"), "from-file")

    def test_readers_use_the_page(self):
        setattr(constance_config, constance_key("show_disabled"), True)
        self.assertTrue(executors._should_show_workflow(type("W", (), {"enabled": False})()))  # pylint: disable=protected-access
        setattr(constance_config, constance_key("builder_model_allowlist"), ["dcim.device"])
        self.assertTrue(content_type_allowed("dcim.device"))
        self.assertFalse(content_type_allowed("ipam.prefix"))


@override_settings(PLUGINS_CONFIG=_plugins())
class SettingsPageTestCase(_ResetSettings, TestCase):
    """The page itself: admins only, saves to Constance, locks file settings."""

    def setUp(self):
        self.admin = User.objects.create_user(username="settings-admin", password="x", is_superuser=True, is_staff=True)
        self.user = User.objects.create_user(username="settings-user", password="x")
        self.url = reverse(URL_NAME)

    def test_only_admins_can_open_it(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for text in ("Repository folders", "Scripts folder", "Publishing to Git", "Committer name", "Save settings"):
            self.assertContains(response, text)

    def test_saving_changes_the_settings(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            _post_data(
                scripts_dir="/automation/scripts/",
                builder_model_allowlist="dcim.*\nextras.tag",
                git_push_retry_backoff_minutes="2, 10",
                git_committer_name="NetOps bot",
            ),
        )
        self.assertRedirects(
            response,
            self.url,
            fetch_redirect_response=False,
            msg_prefix=str(getattr(response, "context", {}) and response.context["form"].errors),
        )
        self.assertEqual(get_setting("scripts_dir"), "automation/scripts")
        self.assertEqual(get_setting("builder_model_allowlist"), ["dcim.*", "extras.tag"])
        self.assertEqual(get_setting("git_push_retry_backoff_minutes"), [2, 10])
        self.assertEqual(get_setting("git_committer_name"), "NetOps bot")

    def test_bad_values_are_explained(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            _post_data(
                scripts_dir="../outside", builder_model_allowlist="not a model", git_push_retry_backoff_minutes="soon"
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a folder inside the repository")
        self.assertContains(response, "isn&#x27;t an object type")
        self.assertContains(response, "Enter whole minutes")
        self.assertEqual(get_setting("scripts_dir"), DEFAULTS["scripts_dir"])

    def test_file_settings_are_locked(self):
        with override_settings(PLUGINS_CONFIG=_plugins(scripts_dir="locked/scripts")):
            self.client.force_login(self.admin)
            response = self.client.get(self.url)
            self.assertContains(response, "Set in nautobot_config.py")
            self.assertContains(response, "shown here but")
            self.client.post(self.url, _post_data(scripts_dir="other", workflow_dir="flows"))
            self.assertEqual(get_setting("scripts_dir"), "locked/scripts")
            self.assertEqual(getattr(constance_config, constance_key("scripts_dir")), DEFAULTS["scripts_dir"])
            self.assertEqual(get_setting("workflow_dir"), "flows")

    def test_changing_the_sync_interval_updates_the_schedule(self):
        ensure_git_maintenance_schedule(django_apps)
        if not ScheduledJob.objects.filter(name=GIT_SCHEDULE_NAME).exists():
            self.skipTest("Git maintenance job is not registered in this database.")
        self.client.force_login(self.admin)
        self.client.post(self.url, _post_data(git_sync_interval_minutes=15))
        self.assertEqual(ScheduledJob.objects.get(name=GIT_SCHEDULE_NAME).crontab, "*/15 * * * *")
