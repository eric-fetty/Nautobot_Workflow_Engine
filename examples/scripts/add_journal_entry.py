"""Write a journal entry on a device."""

PARAMETERS = {
    "device": {"type": "object", "model": "dcim.Device", "required": True, "help": "Device that gets the note."},
    "message": {"type": "string", "required": True, "help": "Text of the journal entry."},
}


def run(params):
    """Add a Nautobot journal entry to the device."""
    from nautobot.dcim.models import Device
    from nautobot.extras.models import JournalEntry

    device = Device.objects.get(pk=params["device"])
    JournalEntry.objects.create(assigned_object=device, comments=params["message"])
    return {"device": device.name}
