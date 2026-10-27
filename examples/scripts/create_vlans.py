"""Create VLANs at a location from a table of rows."""

PARAMETERS = {
    "location": {"type": "object", "model": "dcim.Location", "required": True, "help": "Location the VLANs belong to."},
    "vlans": {"type": "list", "required": True, "help": "Rows with vid and name."},
    "status": {"type": "string", "required": False, "default": "active", "help": "Status of each new VLAN."},
    "tags": {"type": "list", "required": False, "help": "Tags added to every new VLAN."},
}


def run(params):
    """Create each VLAN and return the VLAN IDs."""
    from nautobot.dcim.models import Location
    from nautobot.extras.models import Status
    from nautobot.ipam.models import VLAN

    location = Location.objects.get(pk=params["location"])
    status = Status.objects.get(name__iexact=params.get("status") or "active")
    created = []
    for row in params.get("vlans") or []:
        vlan, _ = VLAN.objects.get_or_create(
            vid=int(row["vid"]), name=row["name"], defaults={"status": status, "location": location}
        )
        created.append(vlan.vid)
    return {"created": created}
