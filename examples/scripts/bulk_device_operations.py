"""Bulk device operations script demonstrating multiobject input type usage."""

from nautobot.dcim.models import Device
from nautobot.extras.models import Status, Tag


def run(*, site, devices, operation, device_status=None, tags=None, tenant=None, confirmation=False, **kwargs):
    """Perform bulk operations on multiple devices.

    Args:
        site: Site object (single selection)
        devices: List of Device IDs (multiobject selection)
        operation: Operation to perform (choice)
        device_status: New status for devices (optional, choice)
        tags: List of tag values to add/remove (optional, multichoice)
        tenant: Tenant object to assign (optional, object)
        confirmation: Boolean confirmation flag
        **kwargs: Additional keyword arguments
    """
    if not confirmation:
        print("❌ Operation cancelled: confirmation required")
        return {"success": False, "message": "Confirmation required"}

    # devices comes as a list of IDs from multiobject input
    if not devices:
        print("❌ No devices selected")
        return {"success": False, "message": "No devices selected"}

    # Fetch device objects
    device_objects = Device.objects.filter(id__in=devices, site=site)
    device_count = device_objects.count()

    if device_count == 0:
        print(f"❌ No devices found in site {site.name}")
        return {"success": False, "message": f"No devices found in site {site.name}"}

    print(f"🔧 Performing '{operation}' on {device_count} device(s) in {site.name}")

    results = []

    if operation == "update_status":
        if not device_status:
            print("❌ Device status is required for update_status operation")
            return {"success": False, "message": "Device status required"}

        status_obj = Status.objects.get(name=device_status)
        for device in device_objects:
            device.status = status_obj
            device.save()
            print(f"  ✅ Updated {device.name} status to {device_status}")
            results.append({"device": device.name, "status": "updated"})

    elif operation == "add_tags":
        if not tags:
            print("❌ Tags are required for add_tags operation")
            return {"success": False, "message": "Tags required"}

        # tags comes as a list of values from multichoice input
        tag_objects = [Tag.objects.get_or_create(name=tag)[0] for tag in tags]

        for device in device_objects:
            for tag in tag_objects:
                device.tags.add(tag)
            device.save()
            print(f"  ✅ Added {len(tag_objects)} tag(s) to {device.name}")
            results.append({"device": device.name, "tags_added": len(tag_objects)})

    elif operation == "remove_tags":
        if not tags:
            print("❌ Tags are required for remove_tags operation")
            return {"success": False, "message": "Tags required"}

        tag_objects = [Tag.objects.get(name=tag) for tag in tags if Tag.objects.filter(name=tag).exists()]

        for device in device_objects:
            for tag in tag_objects:
                device.tags.remove(tag)
            device.save()
            print(f"  ✅ Removed {len(tag_objects)} tag(s) from {device.name}")
            results.append({"device": device.name, "tags_removed": len(tag_objects)})

    elif operation == "assign_tenant":
        if not tenant:
            print("❌ Tenant is required for assign_tenant operation")
            return {"success": False, "message": "Tenant required"}

        for device in device_objects:
            device.tenant = tenant
            device.save()
            print(f"  ✅ Assigned {device.name} to tenant {tenant.name}")
            results.append({"device": device.name, "tenant": tenant.name})

    print(f"\n✅ Bulk operation completed successfully on {device_count} device(s)")

    return {
        "success": True,
        "operation": operation,
        "devices_processed": device_count,
        "site": site.name,
        "results": results,
    }
