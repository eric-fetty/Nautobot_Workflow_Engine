"""Example action script that returns the hostname it receives."""


def run(params):
    """Return the prepared hostname for demonstration purposes."""
    hostname = params["hostname"]
    print(f"Prepared hostname: {hostname}")
    return {"hostname": hostname}
