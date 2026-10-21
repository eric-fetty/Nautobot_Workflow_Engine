"""ServiceNow helpers for scheduling workflows from change windows."""

import dataclasses
import datetime as dt
import logging

import requests
import zoneinfo
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import ExternalIntegration, SecretsGroupAssociation

logger = logging.getLogger(__name__)

DEFAULT_START_FIELD_CANDIDATES = ("start_date",)
DEFAULT_END_FIELD_CANDIDATES = ("end_date",)
DEFAULT_STATE_FIELD_CANDIDATES = ("state",)
DEFAULT_APPROVAL_FIELD_CANDIDATES = ("approval",)
# New: -5.0
# Assess: -4.0
# Authorize: -3.0
# Scheduled: -2.0
# Implement: -1.0
# Review: 0.0
# Closed: 3.0
# Cancelled: 4.0
# Terminal states that can never be used for scheduling. Early lifecycle states
# (new/assess/authorize/review) are allowed at launch; stricter allowed_states
# and approval checks apply at sync/execution when configured.
BLOCKED_STATE_TOKENS = {"cancelled", "canceled", "closed"}
DEFAULT_APPROVED_TOKENS = {"approved"}
VALIDATION_PHASE_LAUNCH = "launch"
VALIDATION_PHASE_SYNC = "sync"
VALIDATION_PHASE_EXECUTION = "execution"
VALIDATION_PHASES = {
    VALIDATION_PHASE_LAUNCH,
    VALIDATION_PHASE_SYNC,
    VALIDATION_PHASE_EXECUTION,
}


class ServiceNowValidationError(Exception):
    """Raised when a ServiceNow change cannot be used for scheduling."""


@dataclasses.dataclass
class ServiceNowChangeWindow:
    """Normalized ServiceNow change data used for workflow scheduling."""

    change_number: str
    sys_id: str
    state_value: str
    state_display: str
    window_start: dt.datetime
    window_end: dt.datetime
    raw: dict


def normalize_change_ticket(change_ticket):
    """Normalize a user-supplied ServiceNow change ticket number."""
    if change_ticket is None:
        return ""
    return str(change_ticket).strip().upper()


def integration_name_for_workflow(workflow, default="ServiceNow"):
    """Return the External Integration name configured on a workflow.

    Workflows that omit ``schedule.servicenow.integration_name`` keep using the
    historical ``ServiceNow`` integration.

    Args:
        workflow: Workflow instance or any object with ``servicenow_schedule_config``.
        default: Name used when the schedule block does not set one.

    Returns:
        External Integration name.
    """
    from .builders import integration_name_from_definition

    config = getattr(workflow, "servicenow_schedule_config", None) or {}
    if not isinstance(config, dict):
        return default
    return integration_name_from_definition({"schedule": {"servicenow": config}}, default=default)


def _normalize_token(value):
    """Normalize free-form tokens for case-insensitive comparisons."""
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _get_servicenow_integration(name="ServiceNow"):
    """Resolve the Nautobot ExternalIntegration used for ServiceNow lookups."""
    try:
        integration = ExternalIntegration.objects.get(name=name)
    except ExternalIntegration.DoesNotExist as exc:
        raise ServiceNowValidationError(f"External Integration '{name}' was not found.") from exc

    if not integration.remote_url:
        raise ServiceNowValidationError(f"External Integration '{name}' is missing a remote_url.")
    if not integration.secrets_group:
        raise ServiceNowValidationError(f"External Integration '{name}' is missing a Secrets Group.")

    return integration


def _parse_auth(integration):
    """Read username/password credentials from a ServiceNow secrets group."""
    try:
        username = integration.secrets_group.get_secret_value(
            access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
        )
        password = integration.secrets_group.get_secret_value(
            access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_PASSWORD,
        )
    except SecretsGroupAssociation.DoesNotExist as exc:
        raise ServiceNowValidationError(
            f"External Integration '{integration.name}' is missing username/password secrets."
        ) from exc

    if not username or not password:
        raise ServiceNowValidationError(
            f"External Integration '{integration.name}' must provide both username and password secrets."
        )

    return username, password


def _get_config_value(config, key, default=None):
    """Return a typed config value from a workflow's ServiceNow schedule config."""
    value = config.get(key, default)
    return value if value is not None else default


def _coerce_candidate_list(config, key, default):
    """Return configured field-name candidates as a tuple of non-empty strings."""
    raw_value = _get_config_value(config, key, default)
    if isinstance(raw_value, str):
        raw_value = [raw_value]
    if not isinstance(raw_value, (list, tuple)):
        return tuple(default)
    values = [str(item).strip() for item in raw_value if str(item).strip()]
    return tuple(values) or tuple(default)


def _resolve_source_timezone(config):
    """Resolve the timezone used when ServiceNow returns naive timestamps."""
    timezone_name = str(_get_config_value(config, "timezone", "UTC") or "UTC").strip() or "UTC"
    if timezone_name.upper() == "UTC":
        return dt.timezone.utc
    try:
        return zoneinfo.ZoneInfo(timezone_name)
    except zoneinfo.ZoneInfoNotFoundError as exc:
        raise ServiceNowValidationError(f"Invalid ServiceNow schedule timezone '{timezone_name}'.") from exc


def _extract_field_value(raw_value):
    """Return the underlying API value and display label for a response field."""
    if isinstance(raw_value, dict):
        return str(raw_value.get("value") or "").strip(), str(raw_value.get("display_value") or "").strip()
    text_value = str(raw_value or "").strip()
    return text_value, text_value


def _find_first_field(record, candidates):
    """Return the first populated field from a response record."""
    for candidate in candidates:
        if candidate in record:
            raw_value = record.get(candidate)
            value, display = _extract_field_value(raw_value)
            if value or display:
                return candidate, value, display
    return None, "", ""


def _iter_change_records(results):
    """Yield only actual change records and ignore metadata helper rows."""
    for item in results:
        if not isinstance(item, dict):
            continue
        if "__meta" in item:
            continue
        if "number" in item or "sys_id" in item:
            yield item


def _parse_datetime_value(raw_value, source_timezone, field_name):
    """Parse a ServiceNow datetime field into an aware UTC datetime."""
    parse_attempts = []
    if isinstance(raw_value, dict):
        parse_attempts.extend(
            [
                (str(raw_value.get("value") or "").strip(), dt.timezone.utc),
                (str(raw_value.get("display_value_internal") or "").strip(), source_timezone),
                (str(raw_value.get("display_value") or "").strip(), source_timezone),
            ]
        )
    else:
        parse_attempts.append((str(raw_value or "").strip(), dt.timezone.utc))

    for candidate_value, naive_timezone in parse_attempts:
        if not candidate_value:
            continue
        parsed = parse_datetime(candidate_value)
        if parsed is None:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_timezone)
        return parsed.astimezone(dt.timezone.utc)

    value, _display = _extract_field_value(raw_value)
    if not value:
        raise ServiceNowValidationError(f"ServiceNow change is missing a value for '{field_name}'.")
    raise ServiceNowValidationError(f"ServiceNow change returned an invalid datetime for '{field_name}': {value}")


def _validate_not_closed(state_value, state_display):
    """Validate that a change is not already in a blocked closed/canceled state."""
    normalized_tokens = {_normalize_token(state_value), _normalize_token(state_display)}
    normalized_tokens.discard("")

    if normalized_tokens & BLOCKED_STATE_TOKENS:
        raise ServiceNowValidationError(
            f"ServiceNow change is not schedulable because it is in state '{state_display or state_value}'."
        )


def _validate_allowed_state(config, state_value, state_display):
    """Validate optional stricter allowed-state rules for execution-time checks."""
    normalized_tokens = {_normalize_token(state_value), _normalize_token(state_display)}
    normalized_tokens.discard("")

    allowed_states = _get_config_value(config, "allowed_states", [])
    if isinstance(allowed_states, str):
        allowed_states = [allowed_states]
    if allowed_states:
        allowed_tokens = {_normalize_token(item) for item in allowed_states if _normalize_token(item)}
        if normalized_tokens.isdisjoint(allowed_tokens):
            raise ServiceNowValidationError(
                f"ServiceNow change state '{state_display or state_value}' is not in the allowed scheduling states."
            )


def _validate_approval(config, record):
    """Validate that a change is approved when the workflow requires it.

    Called only during execution-phase validation. Defaults to requiring approval
    unless the workflow explicitly sets ``require_approved: false``.
    """
    require_approved = bool(_get_config_value(config, "require_approved", True))
    if not require_approved:
        return

    approval_candidates = _coerce_candidate_list(config, "approval_fields", DEFAULT_APPROVAL_FIELD_CANDIDATES)
    approval_field, approval_value, approval_display = _find_first_field(record, approval_candidates)
    if not approval_field:
        return

    approval_tokens = {_normalize_token(approval_value), _normalize_token(approval_display)}
    approval_tokens.discard("")
    allowed_approval_tokens = {
        _normalize_token(item)
        for item in _get_config_value(config, "approved_tokens", DEFAULT_APPROVED_TOKENS)
        if _normalize_token(item)
    }
    if approval_tokens.isdisjoint(allowed_approval_tokens):
        raise ServiceNowValidationError(
            f"ServiceNow change approval state '{approval_display or approval_value}' is not approved for scheduling."
        )


def get_change_window(  # pylint: disable=too-many-arguments
    change_ticket,
    workflow,
    integration_name="ServiceNow",
    timeout=15,
    require_future_window=None,
    validation_phase=VALIDATION_PHASE_LAUNCH,
):
    """Fetch and validate a ServiceNow change window for a workflow launch."""
    normalized_ticket = normalize_change_ticket(change_ticket)
    if not normalized_ticket:
        raise ServiceNowValidationError("A ServiceNow change ticket is required.")

    if validation_phase not in VALIDATION_PHASES:
        raise ValueError(f"Unsupported ServiceNow validation phase '{validation_phase}'.")

    config = workflow.servicenow_schedule_config
    integration = _get_servicenow_integration(name=integration_name)
    username, password = _parse_auth(integration)
    source_timezone = _resolve_source_timezone(config)
    state_candidates = _coerce_candidate_list(config, "state_fields", DEFAULT_STATE_FIELD_CANDIDATES)
    approval_candidates = _coerce_candidate_list(config, "approval_fields", DEFAULT_APPROVAL_FIELD_CANDIDATES)
    start_candidates = _coerce_candidate_list(config, "start_fields", DEFAULT_START_FIELD_CANDIDATES)
    end_candidates = _coerce_candidate_list(config, "end_fields", DEFAULT_END_FIELD_CANDIDATES)
    requested_fields = [
        "number",
        "sys_id",
        *state_candidates,
        *approval_candidates,
        *start_candidates,
        *end_candidates,
    ]

    try:
        response = requests.get(
            f"{integration.remote_url.rstrip('/')}/api/now/table/change_request",
            params={
                "sysparm_query": f"number={normalized_ticket}",
                "sysparm_limit": 2,
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": ",".join(dict.fromkeys(requested_fields)),
            },
            auth=(username, password),
            verify=integration.verify_ssl,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("ServiceNow request failed for ticket %s: %s", normalized_ticket, exc)
        raise ServiceNowValidationError("Unable to reach the ServiceNow API for change validation.") from exc

    if response.status_code in {401, 403}:
        raise ServiceNowValidationError("ServiceNow authentication failed for the configured External Integration.")
    if response.status_code >= 400:
        raise ServiceNowValidationError(f"ServiceNow request failed with status code {response.status_code}.")

    payload = response.json()
    results = list(_iter_change_records(payload.get("result") or []))
    if not results:
        raise ServiceNowValidationError(f"ServiceNow change '{normalized_ticket}' was not found.")
    if len(results) > 1:
        raise ServiceNowValidationError(
            f"ServiceNow change lookup for '{normalized_ticket}' returned multiple results."
        )

    record = results[0]
    _state_field, state_value, state_display = _find_first_field(record, state_candidates)
    if not state_value and not state_display:
        raise ServiceNowValidationError("ServiceNow change is missing a state value.")
    _validate_not_closed(state_value, state_display)
    if validation_phase == VALIDATION_PHASE_EXECUTION:
        _validate_allowed_state(config, state_value, state_display)
        _validate_approval(config, record)

    start_field, _start_value, _start_display = _find_first_field(record, start_candidates)
    end_field, _end_value, _end_display = _find_first_field(record, end_candidates)
    if not start_field or not end_field:
        raise ServiceNowValidationError("ServiceNow change is missing the required scheduled start and end fields.")

    window_start = _parse_datetime_value(record[start_field], source_timezone, start_field)
    window_end = _parse_datetime_value(record[end_field], source_timezone, end_field)

    if window_end <= window_start:
        raise ServiceNowValidationError("ServiceNow change window end must be after the start time.")

    if require_future_window is None:
        require_future_window = bool(_get_config_value(config, "require_future_window", True))
    if require_future_window and window_start <= timezone.now():
        raise ServiceNowValidationError("ServiceNow change window start must be in the future.")

    sys_id_value, _sys_id_display = _extract_field_value(record.get("sys_id"))
    number_value, number_display = _extract_field_value(record.get("number"))

    return ServiceNowChangeWindow(
        change_number=number_value or number_display or normalized_ticket,
        sys_id=sys_id_value,
        state_value=state_value,
        state_display=state_display,
        window_start=window_start,
        window_end=window_end,
        raw=record,
    )
