"""Load demo inventory into a development database.

Run it inside the Nautobot container::

    docker exec nautobot-workflow-engine-nautobot-1 nautobot-server shell \
        --command="exec(open('/source/development/seed_demo.py').read())"

It is safe to run again: existing rows are reused.

This script only creates lab inventory and a fake ServiceNow External
Integration. It does not publish workflows. Sync the canonical examples
from https://github.com/eric-fetty/Nautobot-Demo-Workflows (see that
repository's jobs/seed_lab.py, which creates the same inventory for a
lab Nautobot). Keep the two seeds aligned when you change either one.
"""

from django.contrib.contenttypes.models import ContentType
from nautobot.dcim.models import Device, DeviceType, Interface, Location, LocationType, Manufacturer, Platform
from nautobot.extras.models import ExternalIntegration, Role, Status
from nautobot.ipam.models import IPAddress, Namespace, Prefix, VLANGroup

active = Status.objects.get(name="Active")
device_ct = ContentType.objects.get_for_model(Device)
interface_ct = ContentType.objects.get_for_model(Interface)
vlan_ct = ContentType.objects.get(app_label="ipam", model="vlan")
prefix_ct = ContentType.objects.get(app_label="ipam", model="prefix")
ip_ct = ContentType.objects.get(app_label="ipam", model="ipaddress")
for name in ("Active", "Planned", "Staged", "Offline"):
    status = Status.objects.filter(name=name).first()
    if status is not None:
        status.content_types.add(device_ct, interface_ct, vlan_ct, prefix_ct, ip_ct)

# A placeholder ServiceNow connection so the change-window examples have one to point at.
ExternalIntegration.objects.get_or_create(
    name="ServiceNow (demo)", defaults={"remote_url": "https://example.service-now.com", "timeout": 30}
)

site_type, _ = LocationType.objects.get_or_create(name="Site")
site_type.content_types.add(device_ct, vlan_ct, prefix_ct)
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
device_count = 0
for site_index, (code, location) in enumerate(locations.items()):
    prefix, _ = Prefix.objects.get_or_create(
        prefix=f"10.{20 + site_index}.0.0/16", namespace=namespace, defaults={"status": active}
    )
    try:
        VLANGroup.objects.get_or_create(name=f"{code} lab VLANs", defaults={"location": location})
    except Exception as exc:  # Model fields vary slightly across Nautobot 3.x releases.
        print(f"Skipped VLAN group for {code}: {exc}")
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
            device_count += 1
            mgmt, _ = Interface.objects.get_or_create(
                device=device, name="Management1", defaults={"type": "1000base-t", "status": active}
            )
            for if_name in ("Ethernet1/1", "Ethernet1/2", "Ethernet1/48"):
                Interface.objects.get_or_create(
                    device=device, name=if_name, defaults={"type": "10gbase-x-sfpp", "status": active}
                )
            if device.primary_ip4_id:
                continue
            third = 0 if role_name == "Access switch" else 1
            address, _ = IPAddress.objects.get_or_create(
                address=f"10.{20 + site_index}.{third}.{10 + number}/16",
                namespace=namespace,
                defaults={"status": active},
            )
            mgmt.add_ip_addresses(address)
            device.primary_ip4 = address
            device.save()

print("devices", device_count, "locations", Location.objects.count())
