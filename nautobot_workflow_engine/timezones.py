"""Timezone choices for the ServiceNow change-window setting."""

import datetime as dt
from functools import lru_cache

import zoneinfo

DEFAULT_TIMEZONE = "UTC"

# Shown first in the picker, in this order. The rest follow alphabetically.
COMMON_TIMEZONES = (
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Amsterdam",
    "Europe/Berlin",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
)


@lru_cache(maxsize=1)
def available_timezones():
    """Return every IANA zone name this Python knows, plus UTC."""
    names = set(zoneinfo.available_timezones())
    names.add(DEFAULT_TIMEZONE)
    return frozenset(names)


def is_valid_timezone(name):
    """True when ``name`` is one of :func:`available_timezones`. Empty means the default (UTC)."""
    if not isinstance(name, str):
        return False
    name = name.strip()
    return not name or name.upper() == DEFAULT_TIMEZONE or name in available_timezones()


def _offset(name, now):
    try:
        delta = now.astimezone(zoneinfo.ZoneInfo(name)).utcoffset() or dt.timedelta(0)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return ""
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "−"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours:02d}:{rest:02d}"


def timezone_choices(now=None):
    """Return picker rows: common zones first, then all others alphabetically.

    Each row is ``{"name", "offset", "common"}``. Offsets are for ``now`` and only a hint.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    names = available_timezones()
    common = [name for name in COMMON_TIMEZONES if name in names]
    rest = sorted(name for name in names if name not in common)
    return [{"name": name, "offset": _offset(name, now), "common": name in common} for name in common + rest]
