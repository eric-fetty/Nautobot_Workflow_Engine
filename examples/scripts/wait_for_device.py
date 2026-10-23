"""Wait until a device comes back after a reboot."""

PARAMETERS = {
    "device": {"type": "object", "model": "dcim.Device", "required": True, "help": "Device to wait for."},
    "timeout_minutes": {
        "type": "integer",
        "required": False,
        "default": 15,
        "help": "Give up after this many minutes.",
    },
}


def run(params):
    """Pretend to poll the device and report it back online."""
    from nautobot.dcim.models import Device

    device = Device.objects.get(pk=params["device"])
    print(f"Waiting up to {params.get('timeout_minutes', 15)} minutes for {device.name}")
    return {"device": device.name, "online": True}
