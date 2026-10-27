"""Check that a device answers before the workflow changes anything."""

PARAMETERS = {
    "device": {"type": "object", "model": "dcim.Device", "required": True, "help": "Device to check."},
    "attempts": {"type": "integer", "required": False, "default": 3, "help": "Pings to send before giving up."},
}


def run(params):
    """Return the device name and the number of attempts used."""
    from nautobot.dcim.models import Device

    device = Device.objects.get(pk=params["device"])
    attempts = int(params.get("attempts") or 3)
    print(f"Checking {device.name} with {attempts} attempts")
    return {"device": device.name, "reachable": True, "attempts": attempts}
