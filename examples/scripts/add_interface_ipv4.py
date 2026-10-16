"""Example workflow script to add interface with IPv4 address and proper status handling."""

from nautobot.dcim.models import Device, Interface
from nautobot.extras.models import Status
from nautobot.ipam.models import IPAddress, Prefix
from scripts.lib.utils import get_or_create_interface


def run(params):
    """Create interface with IPv4 address assignment and proper status management."""
    # from scripts.lib.utils import get_or_create_interface
    import ipaddress

    # Ensure the status is set to 'Active' when creating a new IPAddress
    status_active = Status.objects.get(name="Active")
    subnet = ipaddress.ip_network(params["address"], strict=False)
    prefix = Prefix.objects.get(prefix=str(subnet.with_prefixlen))

    device = Device.objects.get(pk=params["device"])
    if params["mode"] == "existing":
        iface_id = params["interface_obj"]
        iface_name = Interface.objects.get(pk=iface_id).name
    else:
        iface_name = params["interface_name"]
    iface = get_or_create_interface(device, iface_name, status_active)
    ip, _ = IPAddress.objects.get_or_create(address=params["address"], status=status_active, parent=prefix)
    iface.ip_addresses.add(ip)
    iface.save()
