"""Run history search, object references and the "Workflow runs" panel on object pages."""

import importlib
import uuid

from constance import config as constance_config
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from nautobot.dcim.models import Device, DeviceType, Location, LocationType, Manufacturer
from nautobot.extras.models import Role, Status
from nautobot.ipam.models import Namespace, Prefix
from nautobot.users.models import ObjectPermission
from nautobot.virtualization.models import Cluster, ClusterType, VirtualMachine

from nautobot_workflow_engine.datasources import save_workflow_definition
from nautobot_workflow_engine.filters import WorkflowRunFilterSet
from nautobot_workflow_engine.forms import WorkflowEngineSettingsForm
from nautobot_workflow_engine.models import Workflow, WorkflowRun, WorkflowRunObjectReference
from nautobot_workflow_engine.run_index import build_run_index, object_ids, sync_run_index
from nautobot_workflow_engine.settings_helpers import DEFAULTS, constance_key, run_history_object_types

User = get_user_model()
VIEW_RUNS = "Workflow Engine: View runs"


def _definition():
    return {
        "key": "port-turnup",
        "name": "Port turn-up",
        "description": "",
        "category": "Lab",
        "approval_required": False,
        "inputs": [
            {"key": "ticket", "label": "Change ticket", "type": "string"},
            {
                "key": "speed",
                "label": "Speed",
                "type": "choice",
                "choices": [{"value": "10g", "label": "Ten gig"}, {"value": "1g", "label": "One gig"}],
            },
            {"key": "apply", "label": "Apply", "type": "boolean"},
            {"key": "device", "label": "Device", "type": "object", "model": "dcim.Device"},
            {"key": "vms", "label": "VMs", "type": "multiobject", "model": "virtualization.VirtualMachine"},
            {"key": "vlans", "label": "VLAN IDs", "type": "list"},
        ],
        "actions": [{"type": "awx", "name": "push", "label": "Push", "template_id": 3}],
    }


def _lab_objects():
    active, _ = Status.objects.get_or_create(name="Active")
    active.content_types.add(*ContentType.objects.get_for_models(Device, Location, VirtualMachine).values())
    location_type = LocationType.objects.create(name="RS Site")
    location_type.content_types.add(ContentType.objects.get_for_model(Device))
    location = Location.objects.create(name="RS Lab", location_type=location_type, status=active)
    manufacturer = Manufacturer.objects.create(name="RS Vendor")
    device_type = DeviceType.objects.create(manufacturer=manufacturer, model="RS-48")
    role = Role.objects.create(name="RS Leaf")
    role.content_types.add(ContentType.objects.get_for_model(Device))
    device = Device.objects.create(
        name="leaf-rs-01", device_type=device_type, role=role, location=location, status=active
    )
    other_device = Device.objects.create(
        name="leaf-rs-02", device_type=device_type, role=role, location=location, status=active
    )
    cluster = Cluster.objects.create(name="RS Cluster", cluster_type=ClusterType.objects.create(name="RS Type"))
    vm_a = VirtualMachine.objects.create(name="vm-rs-a", cluster=cluster, status=active)
    vm_b = VirtualMachine.objects.create(name="vm-rs-b", cluster=cluster, status=active)
    return device, other_device, vm_a, vm_b


class BuildRunIndexTestCase(TestCase):
    """The pure extraction used by saves and by the backfill migration."""

    def setUp(self):
        self.device_id = uuid.uuid4()
        self.vm_ids = [uuid.uuid4(), uuid.uuid4()]
        self.defs = {
            "ticket": {"input_type": "string", "content_type_id": None, "choices": None},
            "speed": {
                "input_type": "choice",
                "content_type_id": None,
                "choices": [{"value": "10g", "label": "Ten gig"}],
            },
            "apply": {"input_type": "boolean", "content_type_id": None, "choices": None},
            "device": {"input_type": "object", "content_type_id": 11, "choices": None},
            "vms": {"input_type": "multiobject", "content_type_id": 12, "choices": None},
        }

    def _resolver(self, names):
        def resolve(content_type_id, ids):
            return {pk: names[pk] for pk in ids if pk in names}

        return resolve

    def test_object_and_multiobject_become_references(self):
        names = {self.device_id: "leaf-01", self.vm_ids[0]: "vm-a", self.vm_ids[1]: "vm-b"}
        refs, text = build_run_index(
            {
                "ticket": "CHG0042",
                "speed": "10g",
                "apply": True,
                "device": str(self.device_id),
                "vms": [str(pk) for pk in self.vm_ids],
            },
            self.defs,
            self._resolver(names),
        )
        self.assertEqual(
            [(r["input_key"], r["content_type_id"], r["object_id"], r["object_display"]) for r in refs],
            [
                ("device", 11, self.device_id, "leaf-01"),
                ("vms", 12, self.vm_ids[0], "vm-a"),
                ("vms", 12, self.vm_ids[1], "vm-b"),
            ],
        )
        for word in ("CHG0042", "10g", "Ten gig", "leaf-01", "vm-a", "vm-b"):
            self.assertIn(word, text)
        self.assertNotIn("True", text)

    def test_missing_objects_keep_a_nameless_reference(self):
        refs, text = build_run_index({"device": str(self.device_id)}, self.defs, self._resolver({}))
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["object_display"], "")
        self.assertEqual(text, "")

    def test_resolver_errors_do_not_stop_indexing(self):
        def broken(content_type_id, ids):
            raise RuntimeError("lookup failed")

        refs, _ = build_run_index({"device": str(self.device_id), "ticket": "CHG1"}, self.defs, broken)
        self.assertEqual(len(refs), 1)

    def test_inputs_the_workflow_no_longer_defines_are_text_only(self):
        refs, text = build_run_index(
            {"old_field": "legacy-value", "table": [{"port": "Et1", "enabled": True}]}, {}, self._resolver({})
        )
        self.assertEqual(refs, [])
        self.assertIn("legacy-value", text)
        self.assertIn("Et1", text)

    def test_empty_create_new_and_bad_ids_are_ignored(self):
        refs, _ = build_run_index(
            {"device": "__create__", "vms": ["not-a-uuid", None, ""]}, self.defs, self._resolver({})
        )
        self.assertEqual(refs, [])
        self.assertEqual(build_run_index(None, self.defs, self._resolver({})), ([], ""))

    def test_object_ids_accepts_json_and_dicts(self):
        pk = uuid.uuid4()
        self.assertEqual(object_ids(f'["{pk}", "{pk}"]'), [pk])
        self.assertEqual(object_ids({"id": str(pk)}), [pk])


class RunIndexOnSaveTestCase(TestCase):
    """Saving a run fills its references and search text from the workflow's inputs."""

    @classmethod
    def setUpTestData(cls):
        cls.device, cls.other_device, cls.vm_a, cls.vm_b = _lab_objects()
        cls.workflow = save_workflow_definition(_definition(), source_repo=None)
        cls.user = User.objects.create(username="rs-runner")

    def _run(self, **inputs):
        return WorkflowRun.objects.create(
            workflow=self.workflow, requested_by=self.user, inputs=inputs, status="success"
        )

    def test_create_indexes_objects(self):
        run = self._run(ticket="CHG7", device=str(self.device.pk), vms=[str(self.vm_a.pk), str(self.vm_b.pk)])
        refs = WorkflowRunObjectReference.objects.filter(workflow_run=run).order_by("object_display")
        self.assertEqual([r.object_display for r in refs], ["leaf-rs-01", "vm-rs-a", "vm-rs-b"])
        self.assertEqual(refs[0].content_type, ContentType.objects.get_for_model(Device))
        self.assertEqual(refs[0].referenced_object, self.device)
        run.refresh_from_db()
        self.assertIn("CHG7", run.search_text)
        self.assertIn("leaf-rs-01", run.search_text)

    def test_status_updates_do_not_reindex_but_input_changes_do(self):
        run = self._run(device=str(self.device.pk))
        WorkflowRunObjectReference.objects.filter(workflow_run=run).update(object_display="cached")
        run.status = "failed"
        run.save()
        run.save(update_fields=["status"])
        self.assertEqual(WorkflowRunObjectReference.objects.get(workflow_run=run).object_display, "cached")
        run.inputs = {"device": str(self.other_device.pk)}
        run.save()
        self.assertEqual(
            list(WorkflowRunObjectReference.objects.filter(workflow_run=run).values_list("object_id", flat=True)),
            [self.other_device.pk],
        )

    def test_deleted_object_keeps_history(self):
        run = self._run(device=str(self.other_device.pk))
        pk = self.other_device.pk
        self.other_device.delete()
        sync_run_index(run)
        ref = WorkflowRunObjectReference.objects.get(workflow_run=run)
        self.assertEqual(ref.object_id, pk)
        self.assertEqual(ref.object_display, "")


class RunSearchFilterTestCase(TestCase):
    """The run filterset's q search and referenced-object filters."""

    @classmethod
    def setUpTestData(cls):
        cls.device, cls.other_device, cls.vm_a, cls.vm_b = _lab_objects()
        cls.workflow = save_workflow_definition(_definition(), source_repo=None)
        cls.user = User.objects.create(username="alice-rs")
        make = WorkflowRun.objects.create
        cls.run_device = make(
            workflow=cls.workflow,
            requested_by=cls.user,
            status="success",
            inputs={"ticket": "CHG100", "device": str(cls.device.pk), "vlans": ["310"]},
        )
        cls.run_vm = make(
            workflow=cls.workflow,
            requested_by=cls.user,
            status="failed",
            inputs={"ticket": "CHG200", "vms": [str(cls.vm_a.pk)], "speed": "1g"},
            schedule_change_ticket="CHG999",
        )

    def _q(self, **data):
        return set(WorkflowRunFilterSet(data, queryset=WorkflowRun.objects.all()).qs)

    def test_q_matches_values_names_and_run_fields(self):
        self.assertEqual(self._q(q="chg100"), {self.run_device})
        self.assertEqual(self._q(q="leaf-rs-01"), {self.run_device})
        self.assertEqual(self._q(q="vm-rs-a"), {self.run_vm})
        self.assertEqual(self._q(q="One gig"), {self.run_vm})
        self.assertEqual(self._q(q="310"), {self.run_device})
        self.assertEqual(self._q(q="CHG999"), {self.run_vm})
        self.assertEqual(self._q(q="failed"), {self.run_vm})
        self.assertEqual(self._q(q="alice-rs"), {self.run_device, self.run_vm})
        self.assertEqual(self._q(q="Port turn"), {self.run_device, self.run_vm})
        self.assertEqual(self._q(q="no-such-thing"), set())

    def test_referenced_object_filter(self):
        self.assertEqual(self._q(referenced_object=str(self.device.pk)), {self.run_device})
        self.assertEqual(
            self._q(referenced_object=str(self.device.pk), referenced_object_type="dcim.device"), {self.run_device}
        )
        self.assertEqual(
            self._q(referenced_object=str(self.device.pk), referenced_object_type="virtualization.virtualmachine"),
            set(),
        )
        self.assertEqual(self._q(referenced_object_type="virtualization.virtualmachine"), {self.run_vm})
        self.assertEqual(self._q(referenced_object="nope"), set())

    def test_run_referencing_one_object_twice_is_listed_once(self):
        workflow = self.workflow
        run = WorkflowRun.objects.create(
            workflow=workflow,
            requested_by=self.user,
            status="success",
            inputs={"vms": [str(self.vm_b.pk)]},
        )
        WorkflowRunObjectReference.objects.create(
            workflow_run=run,
            content_type=ContentType.objects.get_for_model(VirtualMachine),
            object_id=self.vm_b.pk,
            input_key="other",
        )
        qs = WorkflowRunFilterSet({"referenced_object": str(self.vm_b.pk)}, queryset=WorkflowRun.objects.all()).qs
        self.assertEqual(list(qs), [run])


class RunHistoryViewsTestCase(TestCase):
    """Run history page, global search and the object-page panel honour run view permissions."""

    @classmethod
    def setUpTestData(cls):
        cls.device, cls.other_device, cls.vm_a, cls.vm_b = _lab_objects()
        cls.workflow = save_workflow_definition(_definition(), source_repo=None)
        cls.owner = User.objects.create(username="rs-owner")
        cls.device_run = WorkflowRun.objects.create(
            workflow=cls.workflow,
            requested_by=cls.owner,
            status="success",
            inputs={"ticket": "CHG4242", "device": str(cls.device.pk), "vms": [str(cls.vm_a.pk)]},
        )
        cls.list_url = reverse("plugins:nautobot_workflow_engine:workflow_run_list")

    def _login(self, username, *permission_names, extra=()):
        user = User.objects.create(username=username)
        for name in permission_names:
            ObjectPermission.objects.get(name=name).users.add(user)
        for index, (model, actions) in enumerate(extra):
            permission = ObjectPermission.objects.create(name=f"{username}-{index}", actions=actions)
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(user)
        self.client.force_login(User.objects.get(pk=user.pk))
        return user

    def test_header_search_is_scoped_to_run_history(self):
        self._login("rs-viewer", VIEW_RUNS)
        response = self.client.get(self.list_url)
        self.assertEqual(response.context["content_type"], ContentType.objects.get_for_model(WorkflowRun))
        content = response.content.decode()
        # Core's header search shows the active list's scope badge and submits there.
        self.assertIn("in: Run History", content)
        self.assertIn(f'data-nb-link="{self.list_url}"', content)
        # No second search box on the page.
        self.assertNotIn('id="nwe-run-search"', content)

    def test_q_from_header_search_filters_run_history(self):
        self._login("rs-viewer-q", VIEW_RUNS)
        response = self.client.get(self.list_url, {"q": "CHG4242"})
        self.assertContains(response, "Port turn-up")
        response = self.client.get(self.list_url, {"q": "nothing-matches"})
        self.assertContains(response, "No workflow runs found")

    def test_list_route_resolves_to_standard_name(self):
        self.assertEqual(resolve(self.list_url).view_name, "plugins:nautobot_workflow_engine:workflowrun_list")
        self.assertEqual(reverse("plugins:nautobot_workflow_engine:workflowrun_list"), self.list_url)

    def test_live_search_returns_runs(self):
        self._login("rs-live", VIEW_RUNS)
        response = self.client.get(
            reverse("live_search", kwargs={"path": self.list_url.lstrip("/")}),
            {"q": "CHG4242"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Port turn-up")

    def test_view_all_link_filter_shows_object_name(self):
        self._login("rs-viewer2", VIEW_RUNS, extra=[(Device, ["view"])])
        response = self.client.get(
            self.list_url, {"referenced_object": str(self.device.pk), "referenced_object_type": "dcim.device"}
        )
        self.assertContains(response, "Showing runs that used device leaf-rs-01.")
        self.assertContains(response, "Port turn-up")

    def test_run_history_hides_runs_without_permission(self):
        constrained = ObjectPermission.objects.create(
            name="rs-own-runs", actions=["view"], constraints={"requested_by__username": "someone-else"}
        )
        constrained.object_types.add(ContentType.objects.get_for_model(WorkflowRun))
        user = self._login("rs-constrained")
        constrained.users.add(user)
        response = self.client.get(self.list_url, {"q": "CHG4242"})
        self.assertNotContains(response, "Port turn-up</strong>")

    def test_global_search_finds_runs(self):
        self._login("rs-searcher", VIEW_RUNS)
        response = self.client.get(
            reverse("search"),
            {"q": "leaf-rs-01", "model": "nautobot_workflow_engine.workflowrun"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Port turn-up")
        self.assertContains(response, self.device_run.get_absolute_url())

    def test_global_search_without_permission_finds_nothing(self):
        self._login("rs-nosearch")
        response = self.client.get(
            reverse("search"),
            {"q": "leaf-rs-01", "model": "nautobot_workflow_engine.workflowrun"},
            HTTP_HX_REQUEST="true",
        )
        self.assertNotContains(response, self.device_run.get_absolute_url())

    def test_device_and_vm_pages_show_panel(self):
        self._login("rs-panel", VIEW_RUNS, extra=[(Device, ["view"]), (VirtualMachine, ["view"])])
        for obj in (self.device, self.vm_a):
            response = self.client.get(obj.get_absolute_url())
            self.assertEqual(response.status_code, 200)
            content = response.content.decode()
            self.assertIn("WORKFLOW RUNS", content)
            self.assertIn(self.device_run.get_absolute_url(), content)
            self.assertIn(f"referenced_object={obj.pk}", content)

    def test_panel_lists_only_this_objects_runs(self):
        self._login("rs-panel2", VIEW_RUNS, extra=[(Device, ["view"])])
        response = self.client.get(self.other_device.get_absolute_url())
        self.assertContains(response, "WORKFLOW RUNS")
        self.assertNotContains(response, self.device_run.get_absolute_url())
        self.assertContains(response, "No workflow runs have used this device yet.")

    def test_panel_hidden_without_run_permission(self):
        self._login("rs-devices-only", extra=[(Device, ["view"])])
        response = self.client.get(self.device.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "WORKFLOW RUNS")
        self.assertNotContains(response, self.device_run.get_absolute_url())


class RunHistoryObjectTypesSettingTestCase(TestCase):
    """Which pages show the panel is a Settings-page setting that applies without a restart."""

    def tearDown(self):
        setattr(constance_config, constance_key("run_history_object_types"), DEFAULTS["run_history_object_types"])
        super().tearDown()

    def test_default_types(self):
        self.assertEqual(run_history_object_types(), ["dcim.device", "virtualization.virtualmachine"])

    def test_settings_page_value_is_used(self):
        setattr(constance_config, constance_key("run_history_object_types"), ["IPAM.Prefix", "bad", "ipam.prefix"])
        self.assertEqual(run_history_object_types(), ["ipam.prefix"])

    @override_settings(PLUGINS_CONFIG={"nautobot_workflow_engine": {"run_history_object_types": ["dcim.location"]}})
    def test_nautobot_config_wins_and_locks(self):
        setattr(constance_config, constance_key("run_history_object_types"), ["ipam.prefix"])
        self.assertEqual(run_history_object_types(), ["dcim.location"])
        form = WorkflowEngineSettingsForm()
        self.assertTrue(form.fields["run_history_object_types"].disabled)

    def test_extensions_cover_eligible_types(self):
        module = importlib.import_module("nautobot_workflow_engine.template_content")
        registered = {extension.model for extension in module.template_extensions}
        for label in (
            "dcim.device",
            "virtualization.virtualmachine",
            "ipam.prefix",
            "dcim.location",
            "circuits.circuit",
        ):
            self.assertIn(label, registered)
        self.assertNotIn("nautobot_workflow_engine.workflowrun", registered)
        self.assertNotIn("users.user", registered)

    def test_settings_form_offers_eligible_types(self):
        form = WorkflowEngineSettingsForm()
        values = {value for value, _label in form.fields["run_history_object_types"].choices}
        self.assertIn("ipam.prefix", values)
        self.assertIn("dcim.device", values)
        self.assertEqual(form.initial["run_history_object_types"], ["dcim.device", "virtualization.virtualmachine"])


class RunHistoryPanelFollowsSettingTestCase(TestCase):
    """Adding a type on the Settings page shows the panel there at once; removing one hides it."""

    @classmethod
    def setUpTestData(cls):
        cls.device, _, _, _ = _lab_objects()
        active = Status.objects.get(name="Active")
        active.content_types.add(ContentType.objects.get_for_model(Prefix))
        cls.prefix = Prefix.objects.create(
            prefix="10.77.0.0/24", namespace=Namespace.objects.get(name="Global"), status=active
        )
        definition = _definition()
        definition["key"] = "prefix-flow"
        definition["inputs"].append({"key": "prefix", "label": "Prefix", "type": "object", "model": "ipam.Prefix"})
        workflow = save_workflow_definition(definition, source_repo=None)
        cls.owner = User.objects.create(username="rs-prefix-owner")
        cls.prefix_run = WorkflowRun.objects.create(
            workflow=workflow,
            requested_by=cls.owner,
            status="success",
            inputs={"prefix": str(cls.prefix.pk), "device": str(cls.device.pk)},
        )

    def setUp(self):
        self.admin = User.objects.create(username="rs-admin", is_superuser=True, is_staff=True)
        self.client.force_login(self.admin)

    def tearDown(self):
        setattr(constance_config, constance_key("run_history_object_types"), DEFAULTS["run_history_object_types"])
        super().tearDown()

    def test_add_prefix_on_settings_page(self):
        response = self.client.get(self.prefix.get_absolute_url())
        self.assertNotContains(response, "WORKFLOW RUNS")
        data = WorkflowEngineSettingsForm.initial_from_settings()
        data["show_disabled"] = "on" if data["show_disabled"] else ""
        data["run_history_object_types"] = ["dcim.device", "virtualization.virtualmachine", "ipam.prefix"]
        response = self.client.post(reverse("plugins:nautobot_workflow_engine:config"), data)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(self.prefix.get_absolute_url())
        self.assertContains(response, "WORKFLOW RUNS")
        self.assertContains(response, self.prefix_run.get_absolute_url())

    def test_removing_device_hides_panel(self):
        setattr(constance_config, constance_key("run_history_object_types"), ["virtualization.virtualmachine"])
        response = self.client.get(self.device.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "WORKFLOW RUNS")


class WorkflowsListHeaderSearchTestCase(TestCase):
    """The Workflows list uses the header search scope ("in: Workflows") instead of its own box."""

    @classmethod
    def setUpTestData(cls):
        cls.alpha = save_workflow_definition(_definition(), source_repo=None)
        other = _definition()
        other.update({"key": "vm-patch", "name": "VM patching", "category": "Compute", "description": "Patch VMs"})
        cls.beta = save_workflow_definition(other, source_repo=None)
        for workflow in (cls.alpha, cls.beta):
            workflow.enabled = True
            workflow.save()
        cls.url = reverse("plugins:nautobot_workflow_engine:workflows_list")

    def setUp(self):
        self.client.force_login(User.objects.create(username="wf-admin", is_superuser=True, is_staff=True))

    def test_scope_badge_and_no_custom_box(self):
        response = self.client.get(self.url)
        content = response.content.decode()
        self.assertEqual(response.context["content_type"], ContentType.objects.get_for_model(Workflow))
        self.assertIn("in: Workflows", content)
        self.assertIn(f'data-nb-link="{self.url}"', content)
        self.assertNotIn("nwe-catalog-search", content)

    def test_q_filters_the_list(self):
        response = self.client.get(self.url, {"q": "compute"})
        self.assertContains(response, "VM patching")
        self.assertNotContains(response, "Port turn-up</a>")
        response = self.client.get(self.url, {"q": "port-turnup"})
        self.assertContains(response, "Port turn-up")
        self.assertNotContains(response, "VM patching</a>")

    def test_route_maps_to_workflow_model(self):
        self.assertEqual(resolve(self.url).view_name, "plugins:nautobot_workflow_engine:workflow_catalog")
        response = self.client.get(
            reverse("live_search", kwargs={"path": self.url.lstrip("/")}), {"q": "patch"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, "VM patching")

    def test_global_search_finds_workflows(self):
        response = self.client.get(
            reverse("search"), {"q": "Patch VMs", "model": "nautobot_workflow_engine.workflow"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, "VM patching")


class BackfillMigrationTestCase(TestCase):
    """Migration 0016 indexes runs that existed before the feature."""

    migrate_to = [("nautobot_workflow_engine", "0016_backfill_run_references")]

    def test_backfill(self):
        device, _, vm_a, _ = _lab_objects()
        workflow = save_workflow_definition(_definition(), source_repo=None)
        user = User.objects.create(username="rs-backfill")
        run = WorkflowRun.objects.create(
            workflow=workflow,
            requested_by=user,
            status="success",
            inputs={"ticket": "CHG-OLD", "device": str(device.pk), "vms": [str(vm_a.pk)]},
        )
        # Pretend the run predates indexing.
        WorkflowRunObjectReference.objects.all().delete()
        WorkflowRun.objects.filter(pk=run.pk).update(search_text="")

        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration(*self.migrate_to[0])
        backfill = migration.operations[0].code
        historical_apps = executor.loader.project_state(self.migrate_to).apps
        backfill(historical_apps, None)

        run.refresh_from_db()
        self.assertIn("CHG-OLD", run.search_text)
        self.assertIn("leaf-rs-01", run.search_text)
        self.assertEqual(
            set(WorkflowRunObjectReference.objects.filter(workflow_run=run).values_list("object_display", flat=True)),
            {"leaf-rs-01", "vm-rs-a"},
        )
