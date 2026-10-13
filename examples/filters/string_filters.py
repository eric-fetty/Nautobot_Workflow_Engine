"""Example repo-backed Jinja filters."""

from scripts.lib.text_helpers import normalize_hostname


def normalize_for_dns(value):
    """Normalize a hostname for DNS-safe downstream usage."""
    return normalize_hostname(value).replace("_", "-")


FILTERS = {
    "normalize_for_dns": normalize_for_dns,
}
