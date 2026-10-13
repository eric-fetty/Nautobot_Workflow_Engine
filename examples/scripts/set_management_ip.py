"""Example workflow script to set device management IP address."""


def run(params):
    """Set the primary IPv4 address for a device."""
    from nautobot.dcim.models import Device
    from nautobot.ipam.models import IPAddress

    device = Device.objects.get(pk=params["device"])
    ip = IPAddress.objects.get(address=params["address"])
    device.primary_ip4 = ip
    device.save()
