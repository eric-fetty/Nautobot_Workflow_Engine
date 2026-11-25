"""Change freezes: schedule math, scope matching and enforcement.

Every place a run starts or is scheduled calls :func:`check_launch` (or :func:`check_run`
for a run that already exists). Every edit that could take a workflow out of a freeze
(tags, category) calls :func:`check_scope_escape`. Keep new run or edit paths on these
two functions so the rules stay in one place.

Times: one-time freezes store absolute instants. Recurring freezes expand an RFC 5545
RRULE over local wall-clock times in the freeze's own zone, then convert each start and
end to UTC with ``fold=0``. A start that falls in a skipped hour (spring forward) moves to
the matching instant after the jump; a repeated hour (fall back) uses its first instance.
"""

import datetime as dt
from dataclasses import dataclass, field

import zoneinfo
from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrulestr
from django.utils import timezone

from .timezones import DEFAULT_TIMEZONE, is_valid_timezone

# Expanding a rule never looks further ahead than this, so a fast rule can't run away.
MAX_LOOKAHEAD_DAYS = 400
MAX_DURATION_MINUTES = 366 * 24 * 60
# Runs waiting for a time slot. Blocked runs are history and stay out of this.
WAITING_RUN_STATUSES = ("scheduled", "pending_approval")
OVERRIDE_FREEZE = "nautobot_workflow_engine.override_freeze_workflow"

STATE_ACTIVE = "active"
STATE_UPCOMING = "upcoming"
STATE_EXPIRED = "expired"
STATE_DISABLED = "disabled"
STATE_CHOICES = (
    (STATE_ACTIVE, "Active now"),
    (STATE_UPCOMING, "Upcoming"),
    (STATE_EXPIRED, "Expired"),
    (STATE_DISABLED, "Disabled"),
)
STATE_LABELS = dict(STATE_CHOICES)

# Recurrence presets. The freeze form and freeze_form.js offer the same four.
PRESETS = {
    "weekend": {
        "label": "Every weekend, Fri 17:00 to Mon 06:00",
        "rrule": "FREQ=WEEKLY;BYDAY=FR",
        "start_time": "17:00",
        "duration_minutes": 61 * 60,
    },
    "nightly": {
        "label": "Every night, 22:00 to 06:00",
        "rrule": "FREQ=DAILY",
        "start_time": "22:00",
        "duration_minutes": 8 * 60,
    },
    "month_end": {
        "label": "Last weekend of every month",
        "rrule": "FREQ=MONTHLY;BYDAY=-1SA",
        "start_time": "00:00",
        "duration_minutes": 48 * 60,
    },
    "holidays": {
        "label": "Every year, Dec 15 to Jan 5",
        "rrule": "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=15",
        "start_time": "00:00",
        "duration_minutes": 21 * 24 * 60,
    },
}

_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class FreezeBlocked(Exception):
    """A run can't start or be scheduled because a change freeze is active."""

    def __init__(self, decision):
        """Keep the decision so callers can show the freeze."""
        self.decision = decision
        super().__init__(decision.message)


class FreezeScopeEscape(ValueError):
    """An edit would take a workflow out of an active or upcoming freeze."""

    def __init__(self, freezes, field_name="tags"):
        """Name the freezes in the message."""
        self.freezes = list(freezes)
        self.field_name = field_name
        super().__init__(scope_escape_message(self.freezes, field_name))


@dataclass
class Window:
    """One occurrence of a freeze, as UTC instants."""

    start: dt.datetime
    end: dt.datetime

    def contains(self, moment):
        """True when ``moment`` is inside ``[start, end)``."""
        return self.start <= moment < self.end


@dataclass
class FreezeHit:
    """A freeze window that applies to a workflow."""

    freeze: object
    window: Window

    @property
    def start(self):
        """Start of the window (UTC)."""
        return self.window.start

    @property
    def end(self):
        """End of the window (UTC)."""
        return self.window.end


@dataclass
class LaunchDecision:
    """Whether a run may start, and which freeze stops it."""

    hits: list = field(default_factory=list)
    can_override: bool = False
    reason: str = ""

    @property
    def frozen(self):
        """True when any freeze applies."""
        return bool(self.hits)

    @property
    def overridden(self):
        """True when a freeze applies but an override holder gave a reason."""
        return self.frozen and self.can_override and bool(self.reason)

    @property
    def blocked(self):
        """True when the run must not start."""
        return self.frozen and not self.overridden

    @property
    def freeze(self):
        """The first freeze that applies, or None."""
        return self.hits[0].freeze if self.hits else None

    @property
    def message(self):
        """A sentence naming the freeze, for form errors and run logs."""
        if not self.hits:
            return ""
        hit = self.hits[0]
        text = f"Change freeze \u201c{hit.freeze.name}\u201d is in effect until {format_instant(hit.end, hit.freeze)}."
        if hit.freeze.message:
            text = f"{text} {hit.freeze.message}"
        return text


# ---------- time zones and schedule math ----------


def zone_for(name):
    """Return the ZoneInfo for ``name``, or UTC when it is empty or unknown."""
    name = (name or "").strip() or DEFAULT_TIMEZONE
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return dt.timezone.utc


def localize(naive, zone):
    """Attach ``zone`` to a wall-clock time and return the UTC instant (fold=0, see module doc)."""
    return naive.replace(tzinfo=zone, fold=0).astimezone(dt.timezone.utc)


def format_instant(moment, freeze=None, zone_name=None):
    """``2026-12-24 17:00 America/Chicago`` in the freeze's zone (or ``zone_name``)."""
    if moment is None:
        return ""
    name = zone_name or (getattr(freeze, "timezone", "") if freeze is not None else "") or DEFAULT_TIMEZONE
    local = moment.astimezone(zone_for(name))
    return f"{local:%Y-%m-%d %H:%M} {name}"


def _clean_rule_text(text):
    text = (text or "").strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:].strip()
    return text


def build_rule(rule_text, start_date, start_time, until=None):
    """Return a dateutil rule over naive local datetimes. Raises ValueError when it can't parse."""
    text = _clean_rule_text(rule_text)
    if not text:
        raise ValueError("Enter a recurrence rule.")
    if "\n" in text or "\r" in text or "DTSTART" in text.upper() or ":" in text:
        raise ValueError("Enter one rule such as FREQ=WEEKLY;BYDAY=FR, without DTSTART or other lines.")
    if "FREQ=" not in text.upper():
        raise ValueError("The rule needs a FREQ, for example FREQ=WEEKLY;BYDAY=FR.")
    upper = text.upper()
    if "FREQ=SECONDLY" in upper or "FREQ=MINUTELY" in upper:
        raise ValueError("Freezes repeat at most hourly.")
    dtstart = dt.datetime.combine(start_date, start_time).replace(tzinfo=None)
    try:
        rule = rrulestr(text, dtstart=dtstart, ignoretz=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"The rule could not be read: {exc}") from exc
    if not hasattr(rule, "replace"):
        raise ValueError("Enter a single rule.")
    if until is not None:
        try:
            rule = rule.replace(until=dt.datetime.combine(until, dt.time(23, 59, 59)))
        except ValueError as exc:
            raise ValueError(f"The rule could not be read with a last day: {exc}") from exc
    return rule


def _parse_time(value):
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str) and value:
        hours, _, minutes = value.partition(":")
        return dt.time(int(hours), int(minutes or 0))
    return None


def _rule_for(freeze):
    start_date = getattr(freeze, "recur_start_date", None)
    start_time = _parse_time(getattr(freeze, "recur_start_time", None))
    if not start_date or start_time is None or not getattr(freeze, "rrule", ""):
        return None
    try:
        return build_rule(freeze.rrule, start_date, start_time, getattr(freeze, "recur_until", None))
    except ValueError:
        return None


def occurrences(freeze, start, end):
    """Windows of ``freeze`` that overlap ``[start, end)``, in start order, as UTC instants.

    Ignores ``enabled``. A recurring freeze is expanded at most ``MAX_LOOKAHEAD_DAYS`` past ``start``.
    """
    end = min(end, start + dt.timedelta(days=MAX_LOOKAHEAD_DAYS))
    if end <= start:
        return []
    if getattr(freeze, "kind", "") != "recurring":
        begins, ends = getattr(freeze, "starts_at", None), getattr(freeze, "ends_at", None)
        if begins and ends and begins < end and ends > start:
            return [Window(begins, ends)]
        return []
    rule = _rule_for(freeze)
    minutes = getattr(freeze, "duration_minutes", None) or 0
    if rule is None or minutes <= 0:
        return []
    zone = zone_for(getattr(freeze, "timezone", ""))
    length = dt.timedelta(minutes=minutes)
    # Search in local wall-clock time with a day of slack either side for offset changes.
    local_from = start.astimezone(zone).replace(tzinfo=None) - length - dt.timedelta(days=1)
    local_to = end.astimezone(zone).replace(tzinfo=None) + dt.timedelta(days=1)
    windows = []
    for local_start in rule.between(local_from, local_to, inc=True):
        window = Window(localize(local_start, zone), localize(local_start + length, zone))
        if window.start < end and window.end > start:
            windows.append(window)
    return windows


def window_at(freeze, moment):
    """The window of ``freeze`` that contains ``moment``, or None."""
    if getattr(freeze, "kind", "") == "recurring":
        lookback = dt.timedelta(minutes=(getattr(freeze, "duration_minutes", None) or 0) + 1)
    else:
        lookback = dt.timedelta(minutes=MAX_DURATION_MINUTES)
    for window in occurrences(freeze, moment - lookback, moment + dt.timedelta(seconds=1)):
        if window.contains(moment):
            return window
    return None


def next_windows(freeze, after=None, count=5):
    """The next ``count`` windows that end after ``after`` (default now), including a current one."""
    after = after or timezone.now()
    found = []
    for window in occurrences(freeze, after, after + dt.timedelta(days=MAX_LOOKAHEAD_DAYS)):
        found.append(window)
        if len(found) >= count:
            break
    return found


def freeze_state(freeze, now=None):
    """One of active, upcoming, expired or disabled."""
    if not freeze.enabled:
        return STATE_DISABLED
    now = now or timezone.now()
    upcoming = next_windows(freeze, now, count=1)
    if not upcoming:
        return STATE_EXPIRED
    return STATE_ACTIVE if upcoming[0].contains(now) else STATE_UPCOMING


def schedule_errors(freeze):
    """Field errors for a freeze's schedule (works on unsaved objects and plain namespaces)."""
    errors = {}
    tz_name = getattr(freeze, "timezone", "") or ""
    if not is_valid_timezone(tz_name):
        errors["timezone"] = f"Unknown time zone: {tz_name}."
    lead = getattr(freeze, "warning_lead_minutes", 0)
    if lead is None or not isinstance(lead, int) or lead < 0:
        errors["warning_lead_minutes"] = "Enter 0 or more minutes. 0 turns the early warning off."
    kind = getattr(freeze, "kind", "")
    if kind == "recurring":
        if not getattr(freeze, "recur_start_date", None):
            errors["recur_start_date"] = "Choose the first day the freeze can start."
        if _parse_time(getattr(freeze, "recur_start_time", None)) is None:
            errors["recur_start_time"] = "Choose the local start time."
        minutes = getattr(freeze, "duration_minutes", None)
        if not minutes or minutes < 1 or minutes > MAX_DURATION_MINUTES:
            errors["duration_minutes"] = "Each window must last between 1 minute and 366 days."
        until = getattr(freeze, "recur_until", None)
        start_date = getattr(freeze, "recur_start_date", None)
        if until and start_date and until < start_date:
            errors["recur_until"] = "The last day must be on or after the first day."
        if "recur_start_date" not in errors and "recur_start_time" not in errors:
            try:
                build_rule(
                    getattr(freeze, "rrule", ""),
                    start_date,
                    _parse_time(getattr(freeze, "recur_start_time", None)),
                    until,
                )
            except ValueError as exc:
                errors["rrule"] = str(exc)
    elif kind == "one_time":
        begins, ends = getattr(freeze, "starts_at", None), getattr(freeze, "ends_at", None)
        if not begins:
            errors["starts_at"] = "Choose when the freeze starts."
        if not ends:
            errors["ends_at"] = "Choose when the freeze ends."
        if begins and ends:
            length = ends - begins
            if length <= dt.timedelta(0):
                errors["ends_at"] = "The end must come after the start."
            elif length < dt.timedelta(minutes=1) or length > dt.timedelta(minutes=MAX_DURATION_MINUTES):
                errors["ends_at"] = "A freeze must last between 1 minute and 366 days."
    else:
        errors["kind"] = "Choose One time or Recurring."
    return errors


def describe_schedule(freeze):
    """Plain-language schedule, for example ``Every Fri 17:00 – Mon 06:00, America/Chicago``."""
    tz_name = getattr(freeze, "timezone", "") or DEFAULT_TIMEZONE
    if getattr(freeze, "kind", "") != "recurring":
        begins, ends = getattr(freeze, "starts_at", None), getattr(freeze, "ends_at", None)
        if not begins or not ends:
            return "No dates set"
        zone = zone_for(tz_name)
        return f"{begins.astimezone(zone):%b %-d, %Y %H:%M} \u2013 {ends.astimezone(zone):%b %-d, %Y %H:%M}, {tz_name}"
    start_time = _parse_time(getattr(freeze, "recur_start_time", None))
    minutes = getattr(freeze, "duration_minutes", None) or 0
    rule = _rule_for(freeze)
    if rule is None or start_time is None or not minutes:
        return "Recurring (incomplete)"
    until = getattr(freeze, "recur_until", None)
    tail = f", {tz_name}" + (f", until {until:%b %-d, %Y}" if until else "")
    first = dt.datetime.combine(freeze.recur_start_date, start_time)
    last = first + dt.timedelta(minutes=minutes)
    freq = rule._freq  # pylint: disable=protected-access
    interval = rule._interval  # pylint: disable=protected-access
    weekdays = rule._byweekday or ()  # pylint: disable=protected-access
    nth = rule._bynweekday  # pylint: disable=protected-access
    if interval == 1 and freq == WEEKLY and len(weekdays) == 1 and not nth:
        day = weekdays[0]
        end_day = (day + (last - first).days + (1 if last.time() < first.time() else 0)) % 7
        if (last - first) >= dt.timedelta(days=7):
            return f"Every {_DAY_NAMES[day]} {first:%H:%M} for {_human_minutes(minutes)}{tail}"
        return f"Every {_DAY_NAMES[day]} {first:%H:%M} \u2013 {_DAY_NAMES[end_day]} {last:%H:%M}{tail}"
    if interval == 1 and freq == DAILY and not weekdays and minutes < 24 * 60:
        return f"Every day {first:%H:%M} \u2013 {last:%H:%M}{tail}"
    if interval == 1 and freq == MONTHLY and nth and len(nth) == 1 and nth[0][1] == -1:
        return f"Every month, last {_DAY_NAMES[nth[0][0]]} {first:%H:%M} for {_human_minutes(minutes)}{tail}"
    if interval == 1 and freq == YEARLY and rule._bymonth and rule._bymonthday:  # pylint: disable=protected-access
        if len(rule._bymonth) == 1 and len(rule._bymonthday) == 1:  # pylint: disable=protected-access
            sample = dt.datetime(2001, rule._bymonth[0], rule._bymonthday[0], start_time.hour, start_time.minute)  # pylint: disable=protected-access
            done = sample + dt.timedelta(minutes=minutes)
            return f"Every year, {sample:%b %-d %H:%M} \u2013 {done:%b %-d %H:%M}{tail}"
    return f"{_clean_rule_text(freeze.rrule)} at {first:%H:%M} for {_human_minutes(minutes)}{tail}"


def _human_minutes(minutes):
    days, rest = divmod(int(minutes), 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} h")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts) or "0 min"


# ---------- scope ----------


def _norm_category(value):
    return (value or "").strip().casefold()


def _scope(freeze):
    """Scope sets for a freeze, cached on the instance (works with prefetched M2Ms)."""
    cached = getattr(freeze, "_nwe_scope", None)
    if cached is not None:
        return cached
    scope = {
        "all": bool(getattr(freeze, "applies_to_all", False)),
        "categories": {_norm_category(item) for item in (getattr(freeze, "categories", None) or []) if item},
        "tag_ids": set(),
        "workflow_ids": set(),
        "excluded_ids": set(),
    }
    if getattr(freeze, "pk", None):
        scope["tag_ids"] = {tag.pk for tag in freeze.match_tags.all()}
        scope["workflow_ids"] = {workflow.pk for workflow in freeze.workflows.all()}
        scope["excluded_ids"] = {workflow.pk for workflow in freeze.excluded_workflows.all()}
    freeze._nwe_scope = scope
    return scope


def scope_from_values(applies_to_all=False, categories=(), tag_ids=(), workflow_ids=(), excluded_ids=()):
    """Scope sets for an unsaved freeze (preview endpoint)."""
    return {
        "all": bool(applies_to_all),
        "categories": {_norm_category(item) for item in categories or [] if item},
        "tag_ids": set(tag_ids or []),
        "workflow_ids": set(workflow_ids or []),
        "excluded_ids": set(excluded_ids or []),
    }


def _workflow_tag_ids(workflow):
    return {tag.pk for tag in workflow.tags.all()}


def in_scope(freeze, workflow, tag_ids=None, category=None, scope=None):
    """True when ``freeze`` covers ``workflow`` (optionally with proposed tags or category)."""
    scope = scope or _scope(freeze)
    if workflow.pk in scope["excluded_ids"]:
        return False
    if scope["all"]:
        return True
    if workflow.pk in scope["workflow_ids"]:
        return True
    category = workflow.category if category is None else category
    if scope["categories"] and _norm_category(category) in scope["categories"]:
        return True
    if scope["tag_ids"]:
        tag_ids = _workflow_tag_ids(workflow) if tag_ids is None else set(tag_ids)
        if tag_ids & scope["tag_ids"]:
            return True
    return False


def enabled_freezes():
    """Enabled freezes with their scope prefetched."""
    from .models import ChangeFreeze

    return list(
        ChangeFreeze.objects.filter(enabled=True).prefetch_related("match_tags", "workflows", "excluded_workflows")
    )


def affected_workflows(freeze, queryset=None, scope=None):
    """Workflows (not archived) the freeze covers."""
    from .models import Workflow

    queryset = Workflow.objects.filter(archived=False) if queryset is None else queryset
    return [workflow for workflow in queryset.prefetch_related("tags") if in_scope(freeze, workflow, scope=scope)]


# ---------- lookups for a workflow ----------


def active_freezes_for(workflow, at=None, freezes=None, tag_ids=None):
    """Freeze windows that cover ``workflow`` at ``at`` (default now)."""
    at = at or timezone.now()
    freezes = enabled_freezes() if freezes is None else freezes
    hits = []
    for freeze in freezes:
        if not in_scope(freeze, workflow, tag_ids=tag_ids):
            continue
        window = window_at(freeze, at)
        if window is not None:
            hits.append(FreezeHit(freeze, window))
    hits.sort(key=lambda hit: hit.end, reverse=True)
    return hits


def active_freeze_for(workflow, at=None, freezes=None):
    """The freeze window covering ``workflow`` at ``at`` that ends last, or None."""
    hits = active_freezes_for(workflow, at, freezes)
    return hits[0] if hits else None


def freezes_between(workflow, start, end, freezes=None, tag_ids=None):
    """Every freeze window covering ``workflow`` that overlaps ``[start, end)``, by start."""
    freezes = enabled_freezes() if freezes is None else freezes
    hits = []
    for freeze in freezes:
        if not in_scope(freeze, workflow, tag_ids=tag_ids):
            continue
        hits.extend(FreezeHit(freeze, window) for window in occurrences(freeze, start, end))
    hits.sort(key=lambda hit: hit.start)
    return hits


def starting_soon_for(workflow, now=None, freezes=None, tag_ids=None):
    """Freeze windows that start within each freeze's own warning lead time."""
    now = now or timezone.now()
    freezes = enabled_freezes() if freezes is None else freezes
    hits = []
    for freeze in freezes:
        lead = freeze.warning_lead_minutes or 0
        if lead <= 0 or not in_scope(freeze, workflow, tag_ids=tag_ids):
            continue
        for window in occurrences(freeze, now, now + dt.timedelta(minutes=lead)):
            if window.start > now:
                hits.append(FreezeHit(freeze, window))
                break
    hits.sort(key=lambda hit: hit.start)
    return hits


def launch_windows(workflow, now=None, days=60, freezes=None):
    """Windows for the launch page date picker, with each freeze's warning lead time."""
    now = now or timezone.now()
    rows = []
    for hit in freezes_between(workflow, now, now + dt.timedelta(days=days), freezes):
        rows.append(
            {
                "name": hit.freeze.name,
                "message": hit.freeze.message,
                "start": hit.start.isoformat(),
                "end": hit.end.isoformat(),
                "warning_lead_minutes": hit.freeze.warning_lead_minutes,
            }
        )
    return rows[:200]


# ---------- permissions and launch checks ----------


def can_override_freeze(user, workflow=None):
    """True when ``user`` holds Override change freezes (for ``workflow`` when given)."""
    if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", True):
        return False
    if workflow is None:
        return user.has_perm(OVERRIDE_FREEZE)
    return user.has_perm(OVERRIDE_FREEZE, workflow)


def check_launch(workflow, user, at=None, override_reason="", times=None, freezes=None):
    """Decide whether ``user`` may start or schedule ``workflow``.

    ``times`` lists every instant to check (for a schedule: now, because pre-actions run now,
    and the scheduled time). Default is just ``at`` or now.
    """
    times = [moment for moment in (times or [at or timezone.now()]) if moment is not None]
    freezes = enabled_freezes() if freezes is None else freezes
    hits = []
    seen = set()
    for moment in times:
        for hit in active_freezes_for(workflow, moment, freezes):
            key = (hit.freeze.pk, hit.start)
            if key not in seen:
                seen.add(key)
                hits.append(hit)
    reason = (override_reason or "").strip()
    return LaunchDecision(hits=hits, can_override=bool(hits) and can_override_freeze(user, workflow), reason=reason)


def check_run(workflow_run, at=None):
    """Fire-time check for an existing run.

    An override stamped on the run counts only while the requester still holds the
    Override permission for this workflow.
    """
    return check_launch(
        workflow_run.workflow,
        workflow_run.requested_by,
        at=at,
        override_reason=getattr(workflow_run, "freeze_override_reason", "") or "",
    )


def _append(text, line):
    return f"{text}\n{line}".strip() if text else line


def stamp_override(workflow_run, decision, user, save=True):
    """Record an override on the run and its log."""
    freeze = decision.freeze
    workflow_run.freeze = freeze
    workflow_run.freeze_name = freeze.name[:100] if freeze is not None else ""
    workflow_run.freeze_override_by = user
    workflow_run.freeze_override_reason = decision.reason
    line = f"Change freeze \u201c{workflow_run.freeze_name}\u201d overridden by {user}: {decision.reason}"
    workflow_run.log = _append(workflow_run.log, line)
    workflow_run.user_log = _append(workflow_run.user_log, line)
    if save:
        workflow_run.save(
            update_fields=["freeze", "freeze_name", "freeze_override_by", "freeze_override_reason", "log", "user_log"]
        )


def block_run(workflow_run, decision, when="when it was due to start"):
    """Mark a run Blocked by freeze, link the freeze and say why. Nothing else runs."""
    freeze = decision.freeze
    reason = decision.message
    if getattr(workflow_run, "freeze_override_reason", "") and not decision.can_override:
        reason = (
            f"{reason} The override on this run no longer counts because "
            f"{workflow_run.requested_by} no longer has Override change freezes."
        )
    line = f"Blocked by change freeze {when}. {reason}"
    workflow_run.status = "blocked"
    workflow_run.finished = timezone.now()
    workflow_run.freeze = freeze
    workflow_run.freeze_name = freeze.name[:100] if freeze is not None else ""
    workflow_run.scheduled_job = None
    workflow_run.log = _append(workflow_run.log, line)
    workflow_run.user_log = _append(workflow_run.user_log, line)
    workflow_run.save(update_fields=["status", "finished", "freeze", "freeze_name", "scheduled_job", "log", "user_log"])


# ---------- scope escape ----------


def relevant_freezes(now=None, freezes=None):
    """Enabled freezes that are active now or have a window in the look-ahead period."""
    now = now or timezone.now()
    freezes = enabled_freezes() if freezes is None else freezes
    return [freeze for freeze in freezes if next_windows(freeze, now, count=1)]


def scope_escape_message(freezes, field_name="tags"):
    """The message shown when an edit is refused."""
    names = ", ".join(f"\u201c{freeze.name}\u201d" for freeze in freezes)
    what = "category" if field_name == "category" else "tags"
    plural = "freezes" if len(freezes) != 1 else "freeze"
    return (
        f"This change to the {what} would take the workflow out of change {plural} {names}. "
        "Only people with Override change freezes can do that."
    )


def escaped_freezes(workflow, proposed_tag_ids=None, proposed_category=None, now=None, freezes=None):
    """Freezes (active or upcoming) that cover ``workflow`` now but not with the proposed values."""
    if workflow is None or getattr(workflow, "pk", None) is None:
        return []
    if proposed_tag_ids is None and proposed_category is None:
        return []
    current_tags = _workflow_tag_ids(workflow)
    new_tags = current_tags if proposed_tag_ids is None else set(proposed_tag_ids)
    new_category = workflow.category if proposed_category is None else proposed_category
    escaped = []
    for freeze in relevant_freezes(now, freezes):
        if in_scope(freeze, workflow, tag_ids=current_tags) and not in_scope(
            freeze, workflow, tag_ids=new_tags, category=new_category
        ):
            escaped.append(freeze)
    return escaped


def check_scope_escape(workflow, proposed_tags=None, proposed_category=None, user=None, now=None, freezes=None):
    """Raise FreezeScopeEscape when the edit leaves a freeze and ``user`` can't override.

    ``proposed_tags`` is the full new tag list (Tags or IDs), or None when tags don't change.
    ``proposed_category`` is the new category, or None when it doesn't change. A change that
    keeps the workflow in scope, or only adds it to a freeze, passes. Returns the freezes the
    workflow leaves (empty when it leaves none) for an override holder.
    """
    tag_ids = None
    if proposed_tags is not None:
        tag_ids = {getattr(tag, "pk", tag) for tag in proposed_tags}
    escaped = escaped_freezes(workflow, tag_ids, proposed_category, now=now, freezes=freezes)
    if escaped and not can_override_freeze(user, workflow):
        category_changed = proposed_category is not None and _norm_category(proposed_category) != _norm_category(
            workflow.category
        )
        tags_changed = tag_ids is not None and tag_ids != _workflow_tag_ids(workflow)
        field_name = "category" if category_changed and not tags_changed else "tags"
        raise FreezeScopeEscape(escaped, field_name)
    return escaped


# ---------- banners, lists and affected runs ----------


def banner_rows(workflows, now=None, freezes=None):
    """Active and starting-soon freezes that cover at least one of ``workflows``.

    Returns ``{"active": [...], "soon": [...]}``; each row has the freeze, its window and
    how many of the given workflows it covers.
    """
    now = now or timezone.now()
    freezes = enabled_freezes() if freezes is None else freezes
    workflows = list(workflows)
    tag_cache = {workflow.pk: _workflow_tag_ids(workflow) for workflow in workflows}
    active, soon = [], []
    for freeze in freezes:
        covered = [wf for wf in workflows if in_scope(freeze, wf, tag_ids=tag_cache[wf.pk])]
        if not covered:
            continue
        window = window_at(freeze, now)
        if window is not None:
            active.append(_banner_row(freeze, window, len(covered)))
            continue
        lead = freeze.warning_lead_minutes or 0
        if lead > 0:
            upcoming = occurrences(freeze, now, now + dt.timedelta(minutes=lead))
            upcoming = [item for item in upcoming if item.start > now]
            if upcoming:
                soon.append(_banner_row(freeze, upcoming[0], len(covered)))
    active.sort(key=lambda row: row["end"])
    soon.sort(key=lambda row: row["start"])
    return {"active": active, "soon": soon}


def _banner_row(freeze, window, count):
    return {
        "freeze": freeze,
        "name": freeze.name,
        "message": freeze.message,
        "start": window.start,
        "end": window.end,
        "start_label": format_instant(window.start, freeze),
        "end_label": format_instant(window.end, freeze),
        "timezone": freeze.timezone,
        "count": count,
    }


def runs_in_windows(workflow_ids, windows, queryset=None):
    """Waiting runs (Scheduled, Pending Approval) of ``workflow_ids`` that fall in ``windows``."""
    from .models import WorkflowRun

    if not windows or not workflow_ids:
        return []
    queryset = WorkflowRun.objects.all() if queryset is None else queryset
    candidates = queryset.filter(
        workflow_id__in=list(workflow_ids),
        status__in=WAITING_RUN_STATUSES,
        scheduled_for__gte=min(window.start for window in windows),
        scheduled_for__lt=max(window.end for window in windows),
    ).select_related("workflow", "requested_by")
    return [run for run in candidates if any(window.contains(run.scheduled_for) for window in windows)]


def scheduled_runs_in(freeze, now=None, queryset=None, scope=None):
    """Waiting runs of the freeze's workflows whose scheduled time is inside one of its windows."""
    now = now or timezone.now()
    windows = occurrences(freeze, now, now + dt.timedelta(days=MAX_LOOKAHEAD_DAYS))
    workflow_ids = [workflow.pk for workflow in affected_workflows(freeze, scope=scope)]
    return runs_in_windows(workflow_ids, windows, queryset)


def check_sync_category(workflow, definition):
    """Raise FreezeScopeEscape when a Git file's category would take ``workflow`` out of a freeze.

    Git sync has no interactive override: someone with Override change freezes changes the
    category in Nautobot (or adjusts the freeze), then syncs again.
    """
    if workflow is None or getattr(workflow, "pk", None) is None or not isinstance(definition, dict):
        return
    category = definition.get("category") or ""
    if _norm_category(category) == _norm_category(workflow.category):
        return
    escaped = escaped_freezes(workflow, proposed_category=category)
    if escaped:
        raise FreezeScopeEscape(escaped, "category")


def sync_skip_message(workflow_key, filename, exc):
    """The E-NWE-302 sync log line for a file skipped because of a change freeze."""
    names = ", ".join(f"\u201c{freeze.name}\u201d" for freeze in exc.freezes)
    return (
        f"E-NWE-302 Skipping '{workflow_key}' from {filename}: its category change would take it out of "
        f"change freeze {names}. The stored workflow was left unchanged. Someone with Override change freezes "
        "can change the category in Nautobot or adjust the freeze, then sync again."
    )
