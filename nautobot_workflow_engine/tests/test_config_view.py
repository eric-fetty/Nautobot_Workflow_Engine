"""Unit tests for Workflow Engine configuration settings and config view."""

# pylint: disable=protected-access

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nautobot.apps.views import AdminRequiredMixin, GenericView

from nautobot_workflow_engine import executors, settings_helpers
from nautobot_workflow_engine.forms import WorkflowEngineConfigForm
from nautobot_workflow_engine.settings_helpers import (
    APP_NAME,
    SETTING_KEYS,
    constance_key,
    content_type_matches_add_new_item,
    get_setting,
    is_file_locked,
    normalize_show_add_new_item_labels,
)
from nautobot_workflow_engine.views import WorkflowEngineConfigView


class SettingsHelpersTestCase(unittest.TestCase):
    """Validate settings helper utilities."""

    def test_setting_keys_cover_all_supported_settings(self):
        self.assertEqual(
            set(SETTING_KEYS),
            {
                "workflow_dir",
                "filters_dir",
                "awx_external_integration_name",
                "awx_poll_seconds",
                "show_disabled",
                "show_add_new_item",
            },
        )

    def test_constance_key_format(self):
        self.assertEqual(constance_key("workflow_dir"), "nautobot_workflow_engine__workflow_dir")

    def test_normalize_show_add_new_item_labels(self):
        with patch.object(
            settings_helpers, "_canonicalize_content_type_label", side_effect=lambda label: label.lower()
        ):
            self.assertEqual(
                normalize_show_add_new_item_labels("dcim.device, ipam.prefix"),
                ["dcim.device", "ipam.prefix"],
            )
            self.assertEqual(normalize_show_add_new_item_labels(["dcim.device", "dcim.device"]), ["dcim.device"])
            self.assertEqual(normalize_show_add_new_item_labels(""), [])
            self.assertEqual(normalize_show_add_new_item_labels(None), [])

    def test_content_type_matches_add_new_item_model_and_legacy_name(self):
        content_type = SimpleNamespace(app_label="dcim", model="device", name="device")
        self.assertTrue(content_type_matches_add_new_item(content_type, ["dcim.device"]))
        self.assertTrue(content_type_matches_add_new_item(content_type, ["DCIM.Device"]))
        self.assertFalse(content_type_matches_add_new_item(content_type, ["ipam.prefix"]))
        self.assertFalse(content_type_matches_add_new_item(content_type, []))

    def test_get_setting_delegates_to_nautobot_helper(self):
        with patch.object(settings_helpers, "get_app_settings_or_config", return_value="workflows") as mock_get:
            value = get_setting("workflow_dir", fallback="workflows")

        self.assertEqual(value, "workflows")
        mock_get.assert_called_once_with(APP_NAME, "workflow_dir", fallback="workflows")

    def test_is_file_locked_when_key_present_in_plugins_config(self):
        with patch.object(
            settings_helpers.settings,
            "PLUGINS_CONFIG",
            {APP_NAME: {"workflow_dir": "custom"}},
        ):
            self.assertTrue(is_file_locked("workflow_dir"))
            self.assertFalse(is_file_locked("filters_dir"))

    def test_is_file_locked_false_for_empty_plugins_config(self):
        with patch.object(settings_helpers.settings, "PLUGINS_CONFIG", {APP_NAME: {}}):
            for key in SETTING_KEYS:
                self.assertFalse(is_file_locked(key))


class WorkflowEngineConfigFormTestCase(unittest.TestCase):
    """Validate config form locking and cleaned settings."""

    @contextmanager
    def _form_patches(self, locked_keys=None):
        locked = set(locked_keys or [])

        def _is_locked(key):
            return key in locked

        with (
            patch("nautobot_workflow_engine.forms.ExternalIntegration") as mock_ei,
            patch("nautobot_workflow_engine.forms.is_file_locked", side_effect=_is_locked),
            patch("nautobot_workflow_engine.forms.get_setting", side_effect=self._fake_get_setting),
            patch(
                "nautobot_workflow_engine.forms.iter_object_content_type_choices",
                return_value=[("dcim.device", "dcim | device"), ("ipam.prefix", "ipam | prefix")],
            ),
            patch(
                "nautobot_workflow_engine.forms.normalize_show_add_new_item_labels",
                side_effect=lambda value: list(value or []) if not isinstance(value, str) else [value],
            ),
        ):
            integration_qs = MagicMock()
            integration_qs.order_by.return_value = []
            mock_ei.objects.all.return_value = integration_qs
            yield mock_ei

    @staticmethod
    def _fake_get_setting(key, fallback=None):
        defaults = {
            "workflow_dir": "workflows",
            "filters_dir": "filters",
            "awx_external_integration_name": "AWX",
            "awx_poll_seconds": 5,
            "show_disabled": False,
            "show_add_new_item": [],
        }
        return defaults.get(key, fallback)

    def _build_form(self, data=None, locked_keys=None):
        with self._form_patches(locked_keys=locked_keys):
            if data is None:
                return WorkflowEngineConfigForm()
            return WorkflowEngineConfigForm(data)

    def test_locked_fields_are_disabled(self):
        form = self._build_form(locked_keys=["workflow_dir", "show_disabled"])
        self.assertIn("workflow_dir", form.file_locked_keys)
        self.assertIn("show_disabled", form.file_locked_keys)
        self.assertTrue(form.fields["workflow_dir"].disabled)
        self.assertTrue(form.fields["show_disabled"].disabled)
        self.assertFalse(form.fields["filters_dir"].disabled)

    def test_cleaned_settings_skips_locked_keys(self):
        data = {
            "workflow_dir": "from-post",
            "filters_dir": "custom-filters",
            "awx_external_integration_name": "AWX",
            "awx_poll_seconds": "7",
            "show_disabled": "on",
            "show_add_new_item": ["dcim.device"],
        }

        with self._form_patches(locked_keys=["workflow_dir"]):
            form = WorkflowEngineConfigForm(data)
            self.assertTrue(form.is_valid(), form.errors)
            cleaned = form.cleaned_settings()

        self.assertNotIn("workflow_dir", cleaned)
        self.assertEqual(cleaned["filters_dir"], "custom-filters")
        self.assertEqual(cleaned["awx_poll_seconds"], 7)
        self.assertTrue(cleaned["show_disabled"])
        self.assertEqual(cleaned["show_add_new_item"], ["dcim.device"])

    def test_show_add_new_item_uses_multiselect_choices(self):
        form = self._build_form()
        self.assertIn(("dcim.device", "dcim | device"), form.fields["show_add_new_item"].choices)
        self.assertEqual(form.fields["show_add_new_item"].widget.__class__.__name__, "StaticSelect2Multiple")

    def test_initial_from_settings(self):
        with (
            patch("nautobot_workflow_engine.forms.get_setting", side_effect=self._fake_get_setting),
            patch(
                "nautobot_workflow_engine.forms.normalize_show_add_new_item_labels",
                return_value=["dcim.device"],
            ),
        ):
            initial = WorkflowEngineConfigForm.initial_from_settings()
        self.assertEqual(initial["workflow_dir"], "workflows")
        self.assertEqual(initial["awx_poll_seconds"], 5)
        self.assertEqual(initial["show_add_new_item"], ["dcim.device"])


class WorkflowEngineConfigViewTestCase(unittest.TestCase):
    """Validate admin-only config view get/post behavior."""

    def test_view_uses_admin_required_mixin(self):
        self.assertTrue(issubclass(WorkflowEngineConfigView, AdminRequiredMixin))
        self.assertTrue(issubclass(WorkflowEngineConfigView, GenericView))

    def test_get_renders_form_with_initial(self):
        request = MagicMock()
        form = MagicMock()
        form.file_locked_keys = []
        view = WorkflowEngineConfigView()

        with (
            patch.object(WorkflowEngineConfigForm, "initial_from_settings", return_value={"workflow_dir": "workflows"}),
            patch("nautobot_workflow_engine.views.WorkflowEngineConfigForm", return_value=form) as mock_form,
            patch("nautobot_workflow_engine.views.render", return_value="ok") as mock_render,
        ):
            response = view.get(request)

        self.assertEqual(response, "ok")
        mock_form.assert_called_once()
        mock_render.assert_called_once()
        context = mock_render.call_args.args[2]
        self.assertIs(context["form"], form)
        self.assertEqual(context["file_locked_keys"], [])

    def test_post_writes_unlocked_settings_to_constance(self):
        request = MagicMock()
        request.POST = {"workflow_dir": "custom"}
        form = MagicMock()
        form.is_valid.return_value = True
        form.cleaned_settings.return_value = {
            "workflow_dir": "custom",
            "awx_poll_seconds": 9,
        }
        form.file_locked_keys = []
        constance_config = MagicMock()
        view = WorkflowEngineConfigView()

        with (
            patch("nautobot_workflow_engine.views.WorkflowEngineConfigForm", return_value=form),
            patch("constance.config", constance_config),
            patch("nautobot_workflow_engine.views.messages.success"),
            patch("nautobot_workflow_engine.views.redirect", return_value="redirected") as mock_redirect,
            patch("nautobot_workflow_engine.views.reverse", return_value="/plugins/workflow-engine/config/"),
        ):
            response = view.post(request)

        self.assertEqual(response, "redirected")
        self.assertEqual(constance_config.nautobot_workflow_engine__workflow_dir, "custom")
        self.assertEqual(constance_config.nautobot_workflow_engine__awx_poll_seconds, 9)
        mock_redirect.assert_called_once()

    def test_post_redisplays_form_when_invalid(self):
        request = MagicMock()
        request.POST = {}
        form = MagicMock()
        form.is_valid.return_value = False
        form.file_locked_keys = ["show_disabled"]
        view = WorkflowEngineConfigView()

        with (
            patch("nautobot_workflow_engine.views.WorkflowEngineConfigForm", return_value=form),
            patch("nautobot_workflow_engine.views.render", return_value="invalid") as mock_render,
        ):
            response = view.post(request)

        self.assertEqual(response, "invalid")
        context = mock_render.call_args.args[2]
        self.assertEqual(context["file_locked_keys"], ["show_disabled"])


class SettingsReadersTestCase(unittest.TestCase):
    """Validate runtime readers honor get_setting values."""

    def test_should_show_workflow_respects_show_disabled(self):
        disabled = SimpleNamespace(enabled=False)
        with patch.object(executors, "get_setting", return_value=False):
            self.assertFalse(executors._should_show_workflow(disabled))
        with patch.object(executors, "get_setting", return_value=True):
            self.assertTrue(executors._should_show_workflow(disabled))

    def test_get_workflow_queryset_filters_when_show_disabled_false(self):
        mock_workflow = MagicMock()
        with (
            patch.object(executors, "get_setting", return_value=False),
            patch("nautobot_workflow_engine.models.Workflow", mock_workflow),
        ):
            executors._get_workflow_queryset()
        mock_workflow.objects.filter.assert_called_once_with(enabled=True)

    def test_get_workflow_queryset_includes_all_when_show_disabled_true(self):
        mock_workflow = MagicMock()
        with (
            patch.object(executors, "get_setting", return_value=True),
            patch("nautobot_workflow_engine.models.Workflow", mock_workflow),
        ):
            executors._get_workflow_queryset()
        mock_workflow.objects.all.assert_called_once_with()

    def test_awx_integration_uses_configured_name(self):
        mock_ei = MagicMock()
        with (
            patch.object(executors, "get_setting", return_value="MyAWX"),
            patch.object(executors, "ExternalIntegration", mock_ei),
        ):
            executors._awx_integration()
        mock_ei.objects.get.assert_called_once_with(name="MyAWX")
