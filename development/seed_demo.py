"""Load demo inventory and the two demo workflows into a development database.

Run it inside the Nautobot container::

    docker exec nautobot-workflow-engine-nautobot-1 nautobot-server shell \
        --command="exec(open('/source/development/seed_demo.py').read())"

It is
safe to run again: existing rows are reused and the workflows are republished
only when their definition changed.
"""

import pathlib

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from nautobot.dcim.models import Device, DeviceType, Interface, Location, LocationType, Manufacturer, Platform
from nautobot.extras.models import ExternalIntegration, GitRepository, Role, Status
from nautobot.ipam.models import IPAddress, Namespace, Prefix

from nautobot_workflow_engine.models import Workflow, WorkflowHelper
from nautobot_workflow_engine.services import publish

SCRIPTS = ("check_reachability.py", "wait_for_device.py", "add_journal_entry.py", "create_vlans.py", "echo_hostname.py")
SCRIPT_ROOT = pathlib.Path("/source/examples/scripts")

active = Status.objects.get(name="Active")
device_ct = ContentType.objects.get_for_model(Device)
for name in ("Active", "Planned", "Staged", "Offline"):
    status = Status.objects.filter(name=name).first()
    if status is not None:
        status.content_types.add(device_ct)

# A placeholder ServiceNow connection so the change-window demo has one to point at.
ExternalIntegration.objects.get_or_create(
    name="ServiceNow (demo)", defaults={"remote_url": "https://example.service-now.com", "timeout": 30}
)

site_type, _ = LocationType.objects.get_or_create(name="Site")
site_type.content_types.add(device_ct, ContentType.objects.get(app_label="ipam", model="vlan"))
locations = {}
for name, description in (("AMS01", "Amsterdam DC 1"), ("NYC02", "New York HQ")):
    locations[name], _ = Location.objects.get_or_create(
        name=name, defaults={"location_type": site_type, "status": active, "description": description}
    )

arista, _ = Manufacturer.objects.get_or_create(name="Arista")
cisco, _ = Manufacturer.objects.get_or_create(name="Cisco")
types = {
    "eos": DeviceType.objects.get_or_create(model="DCS-7050SX3", defaults={"manufacturer": arista})[0],
    "ios": DeviceType.objects.get_or_create(model="C9300-48P", defaults={"manufacturer": cisco})[0],
}
platforms = {
    "eos": Platform.objects.get_or_create(name="Arista EOS", defaults={"manufacturer": arista})[0],
    "ios": Platform.objects.get_or_create(name="Cisco IOS-XE", defaults={"manufacturer": cisco})[0],
}
roles = {}
for name in ("Access switch", "Distribution switch"):
    roles[name], _ = Role.objects.get_or_create(name=name)
    roles[name].content_types.add(device_ct)

namespace = Namespace.objects.get(name="Global")
statuses = ["Active", "Active", "Active", "Staged", "Offline", "Active"]
for site_index, (code, location) in enumerate(locations.items()):
    prefix, _ = Prefix.objects.get_or_create(
        prefix=f"10.{20 + site_index}.0.0/16", namespace=namespace, defaults={"status": active}
    )
    plan = [("Access switch", "acc-sw", 6 if code == "AMS01" else 4), ("Distribution switch", "dist-sw", 2)]
    for role_name, abbr, count in plan:
        for number in range(1, count + 1):
            kind = "eos" if (number + site_index) % 2 else "ios"
            name = f"{code.lower()}-{abbr}{number:02d}"
            status_name = statuses[(number - 1) % len(statuses)] if role_name == "Access switch" else "Active"
            device, created = Device.objects.get_or_create(
                name=name,
                defaults={
                    "location": location,
                    "device_type": types[kind],
                    "platform": platforms[kind],
                    "role": roles[role_name],
                    "status": Status.objects.get(name=status_name),
                    "serial": f"SN{site_index}{number:03d}{len(abbr)}",
                },
            )
            if device.primary_ip4_id:
                continue
            interface, _ = Interface.objects.get_or_create(
                device=device, name="Management1", defaults={"type": "1000base-t", "status": active}
            )
            third = 0 if role_name == "Access switch" else 1
            address, _ = IPAddress.objects.get_or_create(
                address=f"10.{20 + site_index}.{third}.{10 + number}/16",
                namespace=namespace,
                defaults={"status": active},
            )
            interface.add_ip_addresses(address)
            device.primary_ip4 = address
            device.save()

repo, _ = GitRepository.objects.get_or_create(
    name="netops-workflows",
    defaults={
        "slug": "netops_workflows",
        "remote_url": "https://git.example.net/netops/workflows.git",
        "branch": "main",
        "provided_contents": ["nautobot_workflow_engine.workflows"],
    },
)
for file_name in SCRIPTS:
    path = SCRIPT_ROOT / file_name
    WorkflowHelper.objects.update_or_create(
        source_repo=repo, file_path=f"scripts/{file_name}", defaults={"content": path.read_text()}
    )

REBOOT = {
    "key": "reboot-device",
    "name": "Reboot a device",
    "description": "Safely reboots a network device inside an approved change window, then confirms it comes back online.",
    "category": "Device operations",
    "manual_duration_minutes": 25,
    "inputs": [
        {
            "key": "location",
            "label": "Location",
            "type": "object",
            "model": "dcim.Location",
            "required": True,
            "help_text": "Where the device lives.",
            "filter": {"query": {"status__name": "Active"}},
        },
        {
            "key": "device",
            "label": "Device",
            "type": "object",
            "model": "dcim.Device",
            "required": True,
            "depends_on": ["location"],
            "help_text": "Only active switches at the chosen location are listed.",
            "filter": {"query": {"location": "{{ inputs.location.id }}", "status__name": "Active"}},
        },
        {
            "key": "reboot_type",
            "label": "Reboot type",
            "type": "choice",
            "required": True,
            "default": "soft",
            "choices": [{"value": "soft", "label": "Soft reload"}, {"value": "hard", "label": "Hard power cycle"}],
        },
        {
            "key": "confirm_power_cycle",
            "label": "I understand this cuts power to the device",
            "type": "boolean",
            "show_if": {"reboot_type": "hard"},
        },
        {
            "key": "change_ticket",
            "label": "Change ticket",
            "type": "string",
            "required": True,
            "placeholder": "CHG0031245",
            "help_text": "Approved ServiceNow change for this work.",
            "validators": [
                {"regex": "^CHG\\d{7}$", "message": "Use the change number, like CHG0031245.", "flags": ["IGNORECASE"]}
            ],
        },
        {
            "key": "wait_for_recovery",
            "label": "Wait for the device to come back online",
            "type": "boolean",
            "default": True,
            "help_text": "Checks reachability for up to 15 minutes after the reboot.",
        },
    ],
    "pre_actions": [
        {
            "type": "nautobot",
            "name": "check_reachable",
            "label": "Check device is reachable",
            "script": "scripts/check_reachability.py",
            "with": {"device": "{{ inputs.device.id }}", "attempts": 3},
        }
    ],
    "actions": [
        {
            "type": "awx",
            "name": "reboot_device",
            "label": "Reboot device",
            "template_id": 42,
            "template_name": "Network · Reboot device",
            "monitor": True,
            "extra_vars": {
                "target_host": "{{ inputs.device.name }}",
                "reboot_mode": "{{ inputs.reboot_type }}",
                "change_ticket": "{{ inputs.change_ticket }}",
            },
        },
        {
            "type": "nautobot",
            "name": "wait_for_device",
            "label": "Wait for device to come back",
            "script": "scripts/wait_for_device.py",
            "when": "{{ inputs.wait_for_recovery }}",
            "with": {"device": "{{ inputs.device.id }}", "timeout_minutes": 15},
        },
    ],
    "post_actions": [
        {
            "type": "nautobot",
            "name": "journal_entry",
            "label": "Add journal entry",
            "script": "scripts/add_journal_entry.py",
            "with": {
                "device": "{{ inputs.device.id }}",
                "message": "Rebooted ({{ inputs.reboot_type }}) under {{ inputs.change_ticket }}",
            },
        }
    ],
    "schedule": {
        "servicenow": {
            "integration_name": "ServiceNow (demo)",
            "change_input_key": "change_ticket",
            "allowed_states": ["Scheduled", "Implement"],
            "timezone": "America/New_York",
            "require_approved": True,
        }
    },
}

VLANS = {
    "key": "add-vlans",
    "name": "Add VLANs to switches",
    "description": "Creates VLANs at a location and adds them to the trunk ports of the switches you pick.",
    "category": "IPAM",
    "manual_duration_minutes": 30,
    "inputs": [
        {
            "key": "location",
            "label": "Location",
            "type": "object",
            "model": "dcim.Location",
            "required": True,
            "filter": {"query": {"status__name": "Active"}},
        },
        {
            "key": "switches",
            "label": "Switches",
            "type": "multiobject",
            "model": "dcim.Device",
            "required": True,
            "depends_on": ["location"],
            "help_text": "Access and distribution switches at the location.",
            "filter": {"query": {"location": "{{ inputs.location.id }}"}},
        },
        {
            "key": "vlans",
            "label": "VLANs",
            "type": "listdict",
            "required": True,
            "min_rows": 1,
            "max_rows": 50,
            "help_text": "One row per VLAN. You can paste rows from a spreadsheet.",
            "columns": [
                {
                    "key": "vid",
                    "label": "VLAN ID",
                    "type": "integer",
                    "required": True,
                    "unique": True,
                    "min_value": 1,
                    "max_value": 4094,
                },
                {
                    "key": "name",
                    "label": "Name",
                    "type": "string",
                    "required": True,
                    "validators": [{"regex": "^[a-z0-9-]+$", "message": "Use lowercase letters, numbers and dashes"}],
                },
                {
                    "key": "role",
                    "label": "Role",
                    "type": "choice",
                    "choices": [
                        {"value": "data", "label": "Data"},
                        {"value": "voice", "label": "Voice"},
                        {"value": "mgmt", "label": "Management"},
                    ],
                },
                {"key": "tagged", "label": "Tagged on trunks", "type": "boolean"},
            ],
        },
        {
            "key": "status",
            "label": "VLAN status",
            "type": "choice",
            "required": True,
            "default": "active",
            "choices": [{"value": "active", "label": "Active"}, {"value": "reserved", "label": "Reserved"}],
        },
        {
            "key": "tags",
            "label": "Tags",
            "type": "multichoice",
            "help_text": "Added to every new VLAN.",
            "choices": [
                {"value": "pci", "label": "PCI"},
                {"value": "critical", "label": "Critical"},
                {"value": "lab", "label": "Lab"},
                {"value": "guest", "label": "Guest"},
            ],
        },
        {
            "key": "mtu",
            "label": "MTU",
            "type": "integer",
            "default": 1500,
            "min_value": 576,
            "max_value": 9216,
            "placeholder": "1500",
        },
        {
            "key": "trunk_ports",
            "label": "Extra trunk ports",
            "type": "list",
            "placeholder": "Ethernet1/49",
            "max_items": 8,
            "help_text": "One interface per line. Leave it empty to use the uplinks only.",
            "validators": [
                {
                    "regex": "^(Ethernet|Port-Channel)\\d+(/\\d+)*$",
                    "message": "Use an interface name like Ethernet1/49.",
                    "flags": ["IGNORECASE"],
                }
            ],
        },
        {
            "key": "change_ticket",
            "label": "Change ticket",
            "type": "string",
            "placeholder": "CHG0031245",
            "validators": [
                {"regex": "^CHG\\d{7}$", "message": "Use the change number, like CHG0031245.", "flags": ["IGNORECASE"]}
            ],
        },
        {"key": "check_first", "label": "Show the config diff before pushing", "type": "boolean", "default": True},
    ],
    "actions": [
        {
            "type": "nautobot",
            "name": "create_vlans",
            "label": "Create the VLANs in Nautobot",
            "script": "scripts/create_vlans.py",
            "with": {
                "location": "{{ inputs.location.id }}",
                "vlans": "{{ inputs.vlans }}",
                "status": "{{ inputs.status }}",
                "tags": "{{ inputs.tags }}",
            },
        },
        {
            "type": "awx",
            "name": "push_vlans",
            "label": "Push VLANs to the switches",
            "template_id": 88,
            "template_name": "Network · Configure VLANs",
            "monitor": True,
            "extra_vars": {
                "target_hosts": "{{ inputs.switches }}",
                "vlans": "{{ inputs.vlans }}",
                "mtu": "{{ inputs.mtu }}",
                "check_mode": "{{ inputs.check_first }}",
            },
        },
    ],
}

User = get_user_model()
admin = User.objects.get(username="admin")
for definition in (REBOOT, VLANS):
    workflow = Workflow.objects.filter(key=definition["key"]).first()
    from nautobot_workflow_engine.schema.normalize import normalize

    canonical = normalize(definition)
    live = workflow.published_revision.definition if workflow and workflow.published_revision_id else None
    if live == canonical:
        print("unchanged", definition["key"])
        continue
    saved, revision = publish(
        user=admin,
        workflow=workflow,
        definition=canonical,
        base_revision=None,
        enable=True,
        repository=repo,
        require_base=False,
        commit_summary="Demo data",
        approval_confirm=True,
    )
    print("published", saved.key, revision.number)

print("devices", Device.objects.count(), "locations", Location.objects.count())
