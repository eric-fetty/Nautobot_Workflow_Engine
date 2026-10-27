"""Dynamic form generation for workflow inputs."""

import json
import logging
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django import forms
from django.utils import timezone
from nautobot.apps.forms import BootstrapMixin, StaticSelect2Multiple
from nautobot.extras.models import ExternalIntegration

from .models import Workflow, WorkflowInput
from .servicenow import normalize_change_ticket
from .settings_helpers import (
    DEFAULTS,
    FILE_OVERRIDE_HELP,
    SETTING_GROUPS,
    SETTING_KEYS,
    SETTING_META,
    get_setting,
    is_file_locked,
)


def _depends_on_attr(value):
    """Return a comma-separated dependency list for the launch form."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        parts = [str(part) for part in value if part not in (None, "")]
    elif value in (None, ""):
        parts = []
    else:
        parts = [str(value)]
    return ",".join(parts)


def _plain_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def _range_sentence(low, high):
    if low is not None and high is not None:
        return f"Between {_plain_number(low)} and {_plain_number(high)}"
    if low is not None:
        return f"At least {_plain_number(low)}"
    if high is not None:
        return f"At most {_plain_number(high)}"
    return ""


def _spreadsheet_formula(value, column_type):
    """Return the leading formula character, or '' when the cell is safe to keep."""
    if isinstance(value, bool) or value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if str(column_type) in ("integer", "number") and re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return ""
    if text[0] in "=+-@":
        return text[0]
    return ""


class DependentChoiceField(forms.ChoiceField):
    """A ChoiceField that doesn't validate choices for dependent fields."""

    def __init__(self, workflow_input=None, *args, **kwargs):
        """Initialize dependent choice field with workflow input reference."""
        self.workflow_input = workflow_input
        super().__init__(*args, **kwargs)

    def validate(self, value):
        """Skip choice validation for dependent fields, object fields, and multichoice - will be validated in form's clean method."""
        if self.workflow_input and (
            self.workflow_input.depends_on
            or self._has_jinja_choices()
            or self._is_object_field()
            or self.workflow_input.input_type == "multichoice"
        ):
            # Only validate that it's not empty if required
            if self.required and not value:
                raise forms.ValidationError(self.error_messages["required"], code="required")

            # For object fields, validate that the value is a valid UUID/ID format
            if self._is_object_field() and value:
                self._validate_object_field_value(value)
        else:
            # For non-dependent fields, use normal validation
            super().validate(value)

    def _has_jinja_choices(self):
        """Check if choices contain Jinja template syntax."""
        if not self.workflow_input or not self.workflow_input.choices:
            return False

        choices_str = str(self.workflow_input.choices)
        return "{{" in choices_str and "}}" in choices_str

    def _is_object_field(self):
        """Check if this is an object field."""
        return (
            self.workflow_input
            and self.workflow_input.input_type in ("object", "multiobject")
            and self.workflow_input.content_type
        )

    def _validate_object_field_value(self, value):
        """Validate that the object field value exists in the database."""
        if not self.workflow_input or not self.workflow_input.content_type:
            return

        # Do not validate for "Create New"
        if value == "__create__":
            return

        # For multiobject, value might be a JSON array
        if self.workflow_input.input_type == "multiobject":
            try:
                values = json.loads(value) if isinstance(value, str) else value
                if not isinstance(values, list):
                    values = [values]
            except (json.JSONDecodeError, ValueError, TypeError):
                values = [value]

            model = self.workflow_input.content_type.model_class()
            for val in values:
                try:
                    model.objects.get(pk=val)
                except (model.DoesNotExist, ValueError):
                    raise forms.ValidationError(
                        f"Invalid selection: Object with ID {val} no longer exists.", code="invalid_choice"
                    )
            return

        # Single object validation
        try:
            model = self.workflow_input.content_type.model_class()
            # Check if the object exists - this will raise an exception if not found
            model.objects.get(pk=value)
        except (model.DoesNotExist, ValueError):
            raise forms.ValidationError(
                "Invalid selection: The selected object no longer exists.", code="invalid_choice"
            )


class DynamicWorkflowForm(forms.Form):
    """Server-side definition; fields are progressively revealed client-side."""

    def __init__(self, workflow: Workflow, *args, inputs=None, **kwargs):
        """Initialize dynamic form with fields based on workflow inputs.

        ``inputs`` supplies unsaved rows for a preview run so nothing is written.
        """
        self._input_rows = inputs
        super().__init__(*args, **kwargs)
        self.workflow = workflow
        rows = self._input_rows if self._input_rows is not None else workflow.inputs.all()
        for wi in rows:
            field = self._build_field(wi)
            if field:
                # Apply any initial value provided to the form for this input key
                if hasattr(self, "initial") and wi.key in (self.initial or {}):
                    # Assign the initial value from the form's initial mapping
                    init_val = self.initial.get(wi.key)
                    field.initial = init_val
                    # Also expose the initial to the client-side enhancer so it
                    # can preserve preselected values even when the JS inserts
                    # a placeholder option or rebuilds the menu.
                    try:
                        if isinstance(init_val, (list, dict)):
                            field.widget.attrs["data-initial"] = json.dumps(init_val)
                        elif init_val is None:
                            field.widget.attrs["data-initial"] = ""
                        else:
                            field.widget.attrs["data-initial"] = str(init_val)
                    except Exception:
                        # Fallback to string cast if JSON serialization fails
                        field.widget.attrs["data-initial"] = str(init_val)
                    # If this is an object field and we have an initial PK, try to
                    # resolve the object and inject a matching option so the
                    # client-side enhancer can display the proper label.
                    try:
                        if wi.input_type == "object" and getattr(wi, "content_type", None):
                            # Handle special '__create__' sentinel used for "Create New"
                            if init_val == "__create__":
                                field.choices = [("__create__", "Create New")]
                            elif init_val:
                                model = wi.content_type.model_class()
                                try:
                                    obj = model.objects.get(pk=init_val)
                                    field.choices = [(str(obj.pk), str(obj))]
                                except Exception as exc:
                                    # If we can't resolve the object, don't inject choices
                                    logging.exception(
                                        "Failed to resolve initial object for workflow input %s: %s", wi.key, exc
                                    )
                    except Exception as exc:
                        # Non-fatal: object resolution failing shouldn't break form
                        logging.exception("Error while attempting to inject object initial for %s: %s", wi.key, exc)

                field.widget.attrs["data-required"] = "true" if wi.required else "false"
                self._present_input(field, wi)
                field.required = False
                if isinstance(wi.visible_when, str) and wi.visible_when.strip():
                    field.widget.attrs["data-show-if-template"] = wi.visible_when
                elif isinstance(wi.visible_when, dict) and wi.visible_when:
                    # Handle multiple show_if conditions
                    conditions = dict(wi.visible_when)

                    # Take the first condition as the primary one (for backward compatibility)
                    first_key = next(iter(conditions))
                    first_val = conditions[first_key]

                    # Convert boolean values to lowercase strings for JavaScript compatibility
                    if isinstance(first_val, list):
                        string_values = []
                        for v in first_val:
                            if isinstance(v, bool):
                                string_values.append(str(v).lower())
                            else:
                                string_values.append(str(v))
                    else:
                        if isinstance(first_val, bool):
                            string_values = [str(first_val).lower()]
                        else:
                            string_values = [str(first_val)]

                    field.widget.attrs["data-show-if-field"] = first_key
                    field.widget.attrs["data-show-if-values"] = "|".join(string_values)

                    # If there are additional conditions, store them separately
                    if len(conditions) > 1:
                        additional_conditions = {k: v for k, v in conditions.items() if k != first_key}
                        # Convert boolean values in additional conditions
                        for key, val in additional_conditions.items():
                            if isinstance(val, bool):
                                additional_conditions[key] = str(val).lower()
                            elif isinstance(val, list):
                                additional_conditions[key] = [
                                    str(v).lower() if isinstance(v, bool) else str(v) for v in val
                                ]

                        field.widget.attrs["data-show-if-conditions"] = json.dumps(additional_conditions)
                # Hidden by default if depends_on, revealed by JS
                if wi.depends_on:
                    # Only set data-depends-on if not already set during field creation
                    if "data-depends-on" not in field.widget.attrs:
                        field.widget.attrs["data-depends-on"] = _depends_on_attr(wi.depends_on)
                self.fields[wi.key] = field

        # Add scheduling fields to every workflow form
        execution_help_text = None
        servicenow_change_input_key = getattr(self.workflow, "servicenow_change_input_key", None)
        if servicenow_change_input_key:
            execution_help_text = (
                "Select ServiceNow Change Window to validate the configured change ticket and schedule "
                "the workflow from that window."
            )

        EXECUTION_CHOICES = [("immediate", "Execute Immediately"), ("scheduled", "Schedule for Later")]
        if servicenow_change_input_key:
            EXECUTION_CHOICES.append(("servicenow", "Use ServiceNow Change Window"))

        self.fields["execution_type"] = forms.ChoiceField(
            choices=EXECUTION_CHOICES,
            initial="immediate",
            widget=forms.RadioSelect(attrs={"class": "execution-type-selector"}),
            label="Select when to run this workflow",
            help_text=execution_help_text,
            required=False,
        )

        from nautobot.core.forms import DateTimePicker

        self.fields["scheduled_datetime"] = forms.DateTimeField(
            required=False,
            widget=DateTimePicker(
                attrs={
                    "type": "datetime-local",
                    "class": "form-control",
                    "data-show-if-execution_type": "scheduled",
                    "data-minutes-ahead": "5",
                }
            ),
            label="Schedule Date & Time",
            help_text="Enter the date and time in your local timezone",
        )

    def clean(self):
        """Validate dependent object fields by updating their choices based on current form data."""
        cleaned_data = super().clean()

        def is_visible(wi):
            for dep in wi.depends_on or []:
                if not cleaned_data.get(dep):
                    return False
            if isinstance(wi.visible_when, str) and wi.visible_when.strip():
                from nautobot_workflow_engine.executors import SafeUser, _render

                context = {"inputs": cleaned_data or {}}
                request_user = getattr(getattr(self, "request", None), "user", None)
                if request_user is not None and getattr(request_user, "is_authenticated", False):
                    context["user"] = SafeUser.from_user(request_user)
                try:
                    rendered = _render(wi.visible_when, context, repo=getattr(self.workflow, "source_repo", None))
                except Exception:
                    return False
                text = str(rendered or "").strip().lower()
                return text not in ("", "false", "none", "0", "no")
            if isinstance(wi.visible_when, dict) and wi.visible_when:
                # Handle multiple show_if conditions - ALL must be satisfied
                for ctrl_key, ctrl_val in wi.visible_when.items():
                    ctrl_vals = ctrl_val if isinstance(ctrl_val, list) else [ctrl_val]
                    current_value = cleaned_data.get(ctrl_key)

                    # Handle boolean fields specially
                    if isinstance(current_value, bool):
                        # Convert boolean to lowercase string for comparison
                        current_str = str(current_value).lower()
                        condition_satisfied = any(str(v).lower() == current_str for v in ctrl_vals)
                    else:
                        # Handle other field types as before
                        condition_satisfied = str(current_value or "") in [str(v) for v in ctrl_vals]

                    # If any condition is not satisfied, field should not be visible
                    if not condition_satisfied:
                        return False

                # All conditions satisfied
                return True
            return True

        # Update choices for dependent object fields based on current form data
        for wi in self.workflow.inputs.all():
            if wi.key not in self.fields:
                continue
            if is_visible(wi):
                # Handle list fields - convert JSON string back to list
                if wi.input_type == "list" and wi.key in cleaned_data:
                    list_value = cleaned_data.get(wi.key, "[]")
                    if isinstance(list_value, str):
                        try:
                            cleaned_data[wi.key] = json.loads(list_value)
                        except (json.JSONDecodeError, ValueError):
                            cleaned_data[wi.key] = []
                # Handle list-of-dictionaries fields - convert JSON string back to list
                if wi.input_type == "listdict" and wi.key in cleaned_data:
                    parsed = self._parse_json_list(cleaned_data.get(wi.key, "[]"))
                    cleaned_data[wi.key] = parsed
                    self._validate_tabular_input(wi, parsed)
                if wi.input_type == "integer" and cleaned_data.get(wi.key) not in (None, ""):
                    self._check_number_range(wi, cleaned_data.get(wi.key))
                if wi.input_type == "list" and isinstance(cleaned_data.get(wi.key), list):
                    self._check_item_count(wi, cleaned_data.get(wi.key))
                # Handle multichoice fields - convert JSON string back to list
                if wi.input_type == "multichoice" and wi.key in cleaned_data:
                    multichoice_value = cleaned_data.get(wi.key, "[]")
                    if isinstance(multichoice_value, str):
                        try:
                            cleaned_data[wi.key] = json.loads(multichoice_value)
                        except (json.JSONDecodeError, ValueError):
                            cleaned_data[wi.key] = []
                # Handle multiobject fields - convert JSON string back to list
                if wi.input_type == "multiobject" and wi.key in cleaned_data:
                    multiobject_value = cleaned_data.get(wi.key, "[]")
                    if isinstance(multiobject_value, str):
                        try:
                            cleaned_data[wi.key] = json.loads(multiobject_value)
                        except (json.JSONDecodeError, ValueError):
                            cleaned_data[wi.key] = []
                value = cleaned_data.get(wi.key)
                if isinstance(value, str):
                    self._apply_pattern_validators(wi.key, value, wi.validators)
            else:
                cleaned_data[wi.key] = None  # Hide field if not visible

        # Validate and convert scheduling fields
        execution_type = cleaned_data.get("execution_type")
        scheduled_datetime = cleaned_data.get("scheduled_datetime")

        if execution_type == "scheduled":
            if not scheduled_datetime:
                raise forms.ValidationError("Scheduled date and time is required for scheduled execution.")

            # Prefer browser-local datetime conversion using posted timezone offset.
            # This keeps scheduling aligned with the user's local clock regardless
            # of Nautobot/server timezone configuration.
            scheduled_datetime_utc = None
            raw_local_dt = (self.data.get("scheduled_datetime") or "").strip()
            raw_offset = (self.data.get("browser_timezone_offset_minutes") or "").strip()

            if raw_local_dt and raw_offset:
                try:
                    local_naive = datetime.fromisoformat(raw_local_dt)
                    offset_minutes = int(raw_offset)
                    utc_naive = local_naive + timedelta(minutes=offset_minutes)
                    scheduled_datetime_utc = timezone.make_aware(utc_naive, dt_timezone.utc)
                except (TypeError, ValueError):
                    scheduled_datetime_utc = None

            # Fallback to Django-parsed value if browser conversion wasn't available.
            if scheduled_datetime_utc is None:
                if scheduled_datetime.tzinfo is None:
                    scheduled_datetime_utc = timezone.make_aware(scheduled_datetime, dt_timezone.utc)
                else:
                    scheduled_datetime_utc = scheduled_datetime.astimezone(dt_timezone.utc)

            # Validate against current UTC time with buffer for processing
            now_utc = timezone.now()
            min_time = now_utc + timezone.timedelta(seconds=30)  # 30 second buffer

            if scheduled_datetime_utc <= min_time:
                raise forms.ValidationError(
                    f"Scheduled time must be in the future. "
                    f"Received: {scheduled_datetime_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC, "
                    f"Current: {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )

            # Store the UTC datetime
            cleaned_data["scheduled_datetime"] = scheduled_datetime_utc

        elif execution_type == "servicenow":
            change_input_key = self.workflow.servicenow_change_input_key
            if not change_input_key:
                raise forms.ValidationError(
                    "This workflow does not define a ServiceNow change-ticket input for ServiceNow-based scheduling."
                )

            if change_input_key not in self.fields:
                raise forms.ValidationError(
                    f"Workflow is configured to use '{change_input_key}' for ServiceNow scheduling, but that input is missing."
                )

            change_ticket = cleaned_data.get(change_input_key)
            normalized_ticket = normalize_change_ticket(change_ticket)
            if not normalized_ticket:
                self.add_error(change_input_key, "A ServiceNow change ticket is required for this timing option.")
            else:
                ticket_regex = self.workflow.servicenow_schedule_config.get("ticket_regex")
                if ticket_regex:
                    try:
                        if re.fullmatch(ticket_regex, normalized_ticket) is None:
                            self.add_error(
                                change_input_key,
                                "The ServiceNow change ticket format does not match this workflow's requirements.",
                            )
                    except re.error:
                        logging.warning("Invalid ServiceNow ticket_regex on workflow %s", self.workflow.pk)
                elif re.fullmatch(r"[A-Za-z0-9_-]{3,128}", normalized_ticket) is None:
                    self.add_error(
                        change_input_key,
                        "Enter a valid ServiceNow change ticket using letters, numbers, hyphens, or underscores.",
                    )

            cleaned_data[change_input_key] = normalized_ticket

        return cleaned_data

    def _parse_json_list(self, raw_value):
        """Coerce a list-like JSON payload into a Python list."""
        if isinstance(raw_value, list):
            return raw_value
        if not raw_value:
            return []
        if isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError, ValueError):
                return []
        return []

    def _apply_pattern_validators(self, field_name, value, validators):
        """Reject a value that misses an author-supplied pattern. Flags use Python names."""
        if not isinstance(validators, list) or not value:
            return
        for validator in validators:
            if not isinstance(validator, dict) or not validator.get("regex"):
                continue
            try:
                regex = re.compile(validator["regex"], flags=self._resolve_regex_flags(validator.get("flags")))
            except re.error:
                self.add_error(field_name, "This field has an invalid pattern.")
                continue
            if regex.search(value) is None:
                self.add_error(
                    field_name,
                    validator.get("message") or "This value does not match the required pattern.",
                )

    def _resolve_regex_flags(self, flag_names):
        """Translate serialized regex flags into Python re flags."""
        flags = 0
        for flag_name in flag_names or []:
            resolved = getattr(re, str(flag_name), None)
            if isinstance(resolved, int):
                flags |= resolved
        return flags

    def _coerce_tabular_value(self, column, value):
        """Normalize a listdict column value to the expected Python type."""
        column_type = str(column.get("type") or "string").lower()

        if column_type == "boolean":
            if isinstance(value, bool):
                return value, None
            if value in (None, ""):
                return False, None
            if isinstance(value, (int, float)):
                return bool(value), None
            normalized = str(value).strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True, None
            if normalized in {"false", "0", "no", "n", "off", ""}:
                return False, None
            return value, f"{column.get('label') or column.get('key') or 'Value'} must be true/false."

        label = column.get("label") or column.get("key") or "Value"
        if column_type == "integer":
            if value in (None, ""):
                return "", None
            if isinstance(value, bool):
                return value, f"{label} must be a whole number."
            if isinstance(value, int):
                return value, None
            if isinstance(value, float):
                if value.is_integer():
                    return int(value), None
                return value, f"{label} must be a whole number."
            text = str(value).strip()
            if text[:1] == "+":
                text = text[1:]
            if text.lstrip("-").isdigit():
                return int(text), None
            if "." in text:
                try:
                    float(text)
                except ValueError:
                    return value, f"{label} must be a number."
                return value, f"{label} must be a whole number."
            return value, f"{label} must be a number."

        if column_type == "number":
            if value in (None, ""):
                return "", None
            if isinstance(value, bool):
                return value, f"{label} must be a number."
            if isinstance(value, (int, float)):
                return float(value), None
            try:
                return float(str(value).strip()), None
            except (TypeError, ValueError):
                return value, f"{label} must be a number."

        if column_type == "choice":
            if value in (None, ""):
                return "", None

            normalized_value = str(value).strip()
            normalized_lookup = normalized_value.lower()
            for choice in column.get("choices") or []:
                choice_value = str(choice.get("value", "")).strip()
                choice_label = str(choice.get("label", choice_value)).strip()
                if normalized_lookup in {choice_value.lower(), choice_label.lower()}:
                    return choice_value, None

            return normalized_value, None

        if value is None:
            return "", None
        return str(value), None

    def _check_number_range(self, wi, value):
        low = getattr(wi, "min_value", None)
        high = getattr(wi, "max_value", None)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if low is not None and number < float(low):
            self.add_error(wi.key, _range_sentence(low, high))
        elif high is not None and number > float(high):
            self.add_error(wi.key, _range_sentence(low, high))

    def _check_item_count(self, wi, values):
        minimum = getattr(wi, "min_items", None)
        maximum = getattr(wi, "max_items", None)
        if minimum is not None and len(values) < int(minimum):
            self.add_error(wi.key, f"Enter at least {int(minimum)} lines.")
        if maximum is not None and len(values) > int(maximum):
            self.add_error(wi.key, f"Enter at most {int(maximum)} lines.")

    def _validate_tabular_input(self, wi, rows):
        """Re-run listdict validation server-side for defense in depth."""
        if not isinstance(rows, list):
            self.add_error(wi.key, "Submitted data must be a list of rows.")
            return

        columns = wi.columns or []
        if not isinstance(columns, list):
            self.add_error(wi.key, "Input columns are misconfigured.")
            return

        unique_values = {
            col.get("key"): {}
            for col in columns
            if isinstance(col, dict) and col.get("unique") is True and col.get("key")
        }

        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                self.add_error(wi.key, f"Row {row_index} must be an object.")
                continue

            for column in columns:
                if not isinstance(column, dict):
                    continue

                column_key = column.get("key")
                if not column_key:
                    continue

                label = column.get("label") or column_key
                column_type = str(column.get("type") or "string").lower()
                required = column.get("required") is True
                value, error = self._coerce_tabular_value(column, row.get(column_key))
                row[column_key] = value

                if required and column_type != "boolean":
                    if value in (None, "") or (isinstance(value, str) and value.strip() == ""):
                        self.add_error(wi.key, f"Row {row_index}, {label}: this value is required.")
                        continue

                if error:
                    self.add_error(wi.key, f"Row {row_index}, {label}: {error}")
                    continue

                formula = _spreadsheet_formula(value, column_type)
                if formula:
                    self.add_error(
                        wi.key,
                        f'Row {row_index}: {label} starts with "{formula}", which looks like a spreadsheet formula',
                    )

                low = column.get("min_value")
                high = column.get("max_value")
                bounded = low not in (None, "") or high not in (None, "")
                if column_type in ("integer", "number") and value not in (None, "") and bounded:
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        number = None
                    too_low = low not in (None, "") and number is not None and number < float(low)
                    too_high = high not in (None, "") and number is not None and number > float(high)
                    if too_low or too_high:
                        if low not in (None, "") and high not in (None, ""):
                            message = (
                                f"Row {row_index}: {label} must be between "
                                f"{_plain_number(low)} and {_plain_number(high)}"
                            )
                        else:
                            sentence = _range_sentence(
                                None if low in (None, "") else low,
                                None if high in (None, "") else high,
                            ).lower()
                            message = f"Row {row_index}: {label} {sentence}"
                        self.add_error(wi.key, message)

                if column_type == "choice" and value not in (None, ""):
                    allowed_values = [
                        str(choice.get("value")) for choice in column.get("choices") or [] if "value" in choice
                    ]
                    if allowed_values and str(value) not in allowed_values:
                        self.add_error(wi.key, f"Row {row_index}, {label}: invalid choice '{value}'.")

                if (
                    value not in (None, "")
                    and isinstance(value, str)
                    and value.strip()
                    and isinstance(column.get("validators"), list)
                ):
                    for validator in column.get("validators"):
                        if not isinstance(validator, dict) or not validator.get("regex"):
                            continue
                        try:
                            regex = re.compile(
                                validator["regex"],
                                flags=self._resolve_regex_flags(validator.get("flags")),
                            )
                        except re.error:
                            self.add_error(wi.key, f"Column '{label}' has an invalid regex validator.")
                            continue
                        if not regex.search(value):
                            self.add_error(
                                wi.key,
                                f"Row {row_index}, {label}: {validator.get('message') or 'format is invalid.'}",
                            )

                if column_key in unique_values and value not in (None, ""):
                    normalized = str(value).strip().lower()
                    if normalized:
                        previous_row = unique_values[column_key].get(normalized)
                        if previous_row is not None:
                            self.add_error(
                                wi.key,
                                f"Row {row_index}, {label}: value must be unique (already used in row {previous_row}).",
                            )
                        else:
                            unique_values[column_key][normalized] = row_index

        minimum = getattr(wi, "min_rows", None)
        maximum = getattr(wi, "max_rows", None)
        if minimum not in (None, "") and len(rows) < int(minimum):
            self.add_error(wi.key, f"Add at least {int(minimum)} rows.")
        if maximum not in (None, "") and len(rows) > int(maximum):
            self.add_error(wi.key, f"Use at most {int(maximum)} rows.")

    def _has_jinja_choices(self, wi):
        """Check if workflow input choices contain Jinja template syntax."""
        if not wi.choices:
            return False

        choices_str = str(wi.choices)
        return "{{" in choices_str and "}}" in choices_str

    def _present_input(self, field, wi):
        """Copy help text and placeholder onto the launch-form widget."""
        help_text = getattr(wi, "help_text", "") or ""
        if help_text:
            field.help_text = help_text
        placeholder = getattr(wi, "placeholder", "") or ""
        if placeholder:
            field.widget.attrs["placeholder"] = placeholder
        if getattr(wi, "input_type", "") == "integer":
            sentence = _range_sentence(getattr(wi, "min_value", None), getattr(wi, "max_value", None))
            if sentence:
                field.help_text = f"{field.help_text} {sentence}".strip()

    def _build_field(self, wi: WorkflowInput):
        # Disable server-side required validation - handle in JavaScript
        required = False  # Always False for server-side, JavaScript will handle validation

        if wi.input_type == "string":
            field = forms.CharField(label=wi.label, required=required, initial=wi.default)
            field.widget.attrs.update(
                {
                    "class": "form-control",
                    "data-required": "true" if wi.required else "false",
                }
            )

            # Add validators as data attribute for JavaScript
            if wi.validators:
                field.widget.attrs["data-validators"] = json.dumps(wi.validators)

            return field

        if wi.input_type == "integer":
            field = forms.IntegerField(label=wi.label, required=required, initial=wi.default)
            field.widget.attrs.update(
                {
                    "class": "form-control",
                    "data-required": "true" if wi.required else "false",
                }
            )

            # Add validators as data attribute for JavaScript
            if wi.validators:
                field.widget.attrs["data-validators"] = json.dumps(wi.validators)
            if getattr(wi, "min_value", None) is not None:
                field.widget.attrs["min"] = wi.min_value
                field.widget.attrs["data-min-value"] = wi.min_value
            if getattr(wi, "max_value", None) is not None:
                field.widget.attrs["max"] = wi.max_value
                field.widget.attrs["data-max-value"] = wi.max_value
            return field

        if wi.input_type == "boolean":
            field = forms.BooleanField(label=wi.label, required=False, initial=bool(wi.default))
            field.widget.attrs.update(
                {
                    "data-required": "true" if wi.required else "false",
                }
            )
            return field

        if wi.input_type in ("choice", "multichoice"):
            # Check if choices contain Jinja templates or field has dependencies
            has_jinja = self._has_jinja_choices(wi)
            has_dependencies = bool(wi.depends_on)

            if has_jinja or has_dependencies or wi.input_type == "multichoice":
                # Use DependentChoiceField for dynamic choices and ALL multichoice fields
                field = DependentChoiceField(workflow_input=wi, label=wi.label, required=required, choices=[])
                field.widget.attrs.update(
                    {
                        "data-enhance": "dropdown",
                        "class": "form-control",
                        "data-required": "true" if wi.required else "false",
                    }
                )
                if has_jinja:
                    field.widget.attrs["data-jinja-choices"] = "true"
                if has_dependencies:
                    field.widget.attrs["data-depends-on"] = _depends_on_attr(wi.depends_on)
                if wi.input_type == "multichoice":
                    field.widget.attrs["data-multichoice"] = "true"
                    # For static multichoice without dependencies, populate choices now
                    if not has_jinja and not has_dependencies and wi.choices:
                        choices = [(c["value"], c.get("label", c["value"])) for c in wi.choices]
                        field.choices = [("", "Choose…")] + choices
                return field
            else:
                # Standard static choice field (single selection only)
                choices = [(c["value"], c.get("label", c["value"])) for c in (wi.choices or [])]
                field = forms.ChoiceField(label=wi.label, required=required, choices=choices)
                field.widget.attrs.update(
                    {
                        "data-enhance": "dropdown",
                        "class": "form-control",
                        "data-required": "true" if wi.required else "false",
                    }
                )
                return field

        if wi.input_type == "object" and wi.content_type:
            # For object fields, pre-populate with first 200 entries for better UX
            # Users can still search for more specific items
            field = DependentChoiceField(workflow_input=wi, label=wi.label, required=required, choices=[])
            field.widget.attrs.update(
                {
                    "data-enhance": "dropdown",
                    "data-object-field": "true",
                    "data-preload": "true",  # Indicate this field should be pre-loaded
                    "class": "form-control",
                    "data-required": "true" if wi.required else "false",
                }
            )
            if wi.depends_on:
                field.widget.attrs["data-depends-on"] = _depends_on_attr(wi.depends_on)
                # Don't pre-load dependent fields - they'll be loaded when dependencies change
                field.widget.attrs["data-preload"] = "false"
            return field

        if wi.input_type == "multiobject" and wi.content_type:
            # For multiobject fields, allow multiple object selections with checkboxes
            field = DependentChoiceField(workflow_input=wi, label=wi.label, required=required, choices=[])
            field.widget.attrs.update(
                {
                    "data-enhance": "dropdown",
                    "data-object-field": "true",
                    "data-multiobject": "true",
                    "data-preload": "true",  # Indicate this field should be pre-loaded
                    "class": "form-control",
                    "data-required": "true" if wi.required else "false",
                }
            )
            if wi.depends_on:
                field.widget.attrs["data-depends-on"] = _depends_on_attr(wi.depends_on)
                # Don't pre-load dependent fields - they'll be loaded when dependencies change
                field.widget.attrs["data-preload"] = "false"
            return field

        if wi.input_type == "list":
            # List field for adding/removing string values with validation
            field = forms.CharField(label=wi.label, required=required, initial="")
            field.widget.attrs.update(
                {
                    "data-enhance": "list",
                    "data-label": wi.label,
                    "class": "form-control",
                    "data-required": "true" if wi.required else "false",
                    "style": "display: none;",  # Hidden input to store the actual list data
                }
            )

            # Add validators as data attribute for JavaScript
            if wi.validators:
                field.widget.attrs["data-validators"] = json.dumps(wi.validators)

            # Set initial value if provided (should be a list)
            if wi.default:
                if isinstance(wi.default, list):
                    field.initial = json.dumps(wi.default)
                else:
                    field.initial = "[]"
            else:
                field.initial = "[]"

            return field

        if wi.input_type == "listdict":
            # List-of-dictionaries field rendered as a dynamic table in the UI.
            # We use a plain TextInput that is hidden via style. The columns
            # schema is injected into the page by the template as a JSON
            # <script> block (keyed by field name) to avoid any HTML-attribute
            # escaping issues with complex JSON in data-* attributes.
            field = forms.CharField(label=wi.label, required=required, initial="")
            field.widget.attrs.update(
                {
                    "data-enhance": "list-dict",
                    "data-label": wi.label,
                    "data-required": "true" if wi.required else "false",
                    "data-csv-import": "false" if getattr(wi, "csv_import", True) is False else "true",
                    "data-min-rows": "" if getattr(wi, "min_rows", None) is None else wi.min_rows,
                    "data-max-rows": "" if getattr(wi, "max_rows", None) is None else wi.max_rows,
                    "style": "display: none;",
                }
            )

            # Attach columns schema for client-side rendering
            try:
                if wi.columns is not None:
                    field.widget.attrs["data-columns"] = json.dumps(wi.columns)
            except Exception:  # noqa: S110
                logging.exception("Failed to serialize columns for workflow input %s", wi.key)

            # Set initial value if provided (should be a list of dicts)
            if wi.default:
                if isinstance(wi.default, list):
                    field.initial = json.dumps(wi.default)
                else:
                    field.initial = "[]"
            else:
                field.initial = "[]"

            return field
        return None


_MODEL_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*\.(\*|[a-z_][a-z0-9_]*)$")
_EXCLUDED_CONTENT_TYPE_APPS = frozenset(
    {
        "admin",
        "auth",
        "contenttypes",
        "django_celery_beat",
        "django_celery_results",
        "sessions",
        "social_django",
        "taggit",
    }
)


def _object_type_choices(selected):
    """``(app_label.model, label)`` pairs for object types, plus any stored value no longer found."""
    from django.contrib.contenttypes.models import ContentType

    from .settings_helpers import content_type_setting_value

    choices = []
    for content_type in ContentType.objects.order_by("app_label", "model"):
        if content_type.app_label in _EXCLUDED_CONTENT_TYPE_APPS or content_type.model_class() is None:
            continue
        label = getattr(content_type, "app_labeled_name", None) or f"{content_type.app_label} | {content_type.name}"
        choices.append((content_type_setting_value(content_type), label))
    known = {value for value, _label in choices}
    for value in selected or []:
        if value not in known:
            choices.append((value, f"{value} (not found)"))
            known.add(value)
    return choices


def _canonical_object_types(values):
    """Stored object types as ``app_label.model``; older ``app_label.<Verbose name>`` values are mapped too."""
    from django.contrib.contenttypes.models import ContentType

    from .settings_helpers import content_type_setting_value

    lookup = {}
    for content_type in ContentType.objects.all():
        canonical = content_type_setting_value(content_type)
        lookup.setdefault(canonical.lower(), canonical)
        lookup.setdefault(f"{content_type.app_label}.{content_type.name}".lower(), canonical)
    result = []
    for raw in values or []:
        value = str(raw).strip()
        if value:
            value = lookup.get(value.lower(), value)
            if value not in result:
                result.append(value)
    return result


class WorkflowEngineSettingsForm(BootstrapMixin, forms.Form):
    """The Settings page: every Workflow Engine setting, stored with Constance."""

    workflow_dir = forms.CharField(max_length=255)
    filters_dir = forms.CharField(max_length=255)
    scripts_dir = forms.CharField(max_length=255)
    show_disabled = forms.BooleanField(required=False)
    show_add_new_item = forms.MultipleChoiceField(required=False, choices=[], widget=StaticSelect2Multiple())
    builder_model_allowlist = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 6}))
    git_committer_name = forms.CharField(max_length=255)
    git_committer_email = forms.EmailField(max_length=255)
    git_author_email_domain = forms.CharField(max_length=255)
    git_sync_interval_minutes = forms.IntegerField(min_value=1, max_value=60)
    git_remote_timeout_seconds = forms.IntegerField(min_value=1, max_value=300)
    git_push_lock_timeout = forms.IntegerField(min_value=30, max_value=3600)
    git_push_retry_backoff_minutes = forms.CharField(max_length=255)
    awx_external_integration_name = forms.ChoiceField(choices=[])
    awx_poll_seconds = forms.IntegerField(min_value=1, max_value=3600)

    def __init__(self, *args, **kwargs):
        """Fill labels and choices from the settings, and lock what nautobot_config.py sets."""
        kwargs.setdefault("initial", self.initial_from_settings())
        super().__init__(*args, **kwargs)
        for key, field in self.fields.items():
            label, help_text, _group = SETTING_META[key]
            field.label = label
            field.help_text = help_text

        current = get_setting("awx_external_integration_name", "AWX") or "AWX"
        choices = [(ei.name, ei.name) for ei in ExternalIntegration.objects.order_by("name")]
        if current not in {name for name, _label in choices}:
            choices.insert(0, (current, f"{current} (not found)"))
        self.fields["awx_external_integration_name"].choices = choices
        self.fields["show_add_new_item"].choices = _object_type_choices(self.initial.get("show_add_new_item"))

        self.file_locked_keys = []
        for key, field in self.fields.items():
            if is_file_locked(key):
                self.file_locked_keys.append(key)
                field.disabled = True
                field.help_text = f"{field.help_text} {FILE_OVERRIDE_HELP}"

    @staticmethod
    def initial_from_settings():
        """Current values, in the shape the fields show them."""
        values = {key: get_setting(key, DEFAULTS[key]) for key in SETTING_META}
        values["show_disabled"] = bool(values["show_disabled"])
        values["show_add_new_item"] = _canonical_object_types(values["show_add_new_item"])
        values["builder_model_allowlist"] = "\n".join(str(item) for item in values["builder_model_allowlist"] or [])
        values["git_push_retry_backoff_minutes"] = ", ".join(
            str(item) for item in values["git_push_retry_backoff_minutes"] or []
        )
        return values

    def grouped_fields(self):
        """``[(title, [bound fields])]`` in page order."""
        return [
            (title, [self[key] for key in SETTING_KEYS if SETTING_META[key][2] == group])
            for group, title in SETTING_GROUPS
        ]

    def clean_workflow_dir(self):
        """A folder inside the repository."""
        return self._clean_folder("workflow_dir")

    def clean_filters_dir(self):
        """A folder inside the repository."""
        return self._clean_folder("filters_dir")

    def clean_scripts_dir(self):
        """A folder inside the repository."""
        return self._clean_folder("scripts_dir")

    def _clean_folder(self, key):
        value = str(self.cleaned_data.get(key) or "").strip().strip("/")
        if not value or ".." in value.split("/") or "\\" in value:
            raise forms.ValidationError("Enter a folder inside the repository, for example “workflows”.")
        return value

    def clean_builder_model_allowlist(self):
        """One ``app.model`` or ``app.*`` per line, stored as a list."""
        items = []
        for raw in re.split(r"[\n,]", self.cleaned_data.get("builder_model_allowlist") or ""):
            item = raw.strip().lower()
            if not item:
                continue
            if not _MODEL_PATTERN.match(item):
                raise forms.ValidationError(
                    f"“{raw.strip()}” isn't an object type. Use app.model or app.*, for example dcim.*."
                )
            if item not in items:
                items.append(item)
        return items

    def clean_git_push_retry_backoff_minutes(self):
        """Comma-separated whole minutes, stored as a list."""
        parts = [part.strip() for part in str(self.cleaned_data.get("git_push_retry_backoff_minutes") or "").split(",")]
        parts = [part for part in parts if part]
        if not parts or not all(part.isdigit() and 1 <= int(part) <= 1440 for part in parts):
            raise forms.ValidationError(
                "Enter whole minutes between 1 and 1440, separated by commas, for example 1, 5, 15, 60."
            )
        return [int(part) for part in parts]

    def clean_show_add_new_item(self):
        """Deduplicated ``app_label.model`` values."""
        seen, values = set(), []
        for value in self.cleaned_data.get("show_add_new_item") or []:
            if value.lower() not in seen:
                seen.add(value.lower())
                values.append(value)
        return values

    def changed_settings(self):
        """``{key: value}`` for unlocked settings that differ from what is stored now."""
        current = {key: get_setting(key, DEFAULTS[key]) for key in SETTING_META}
        return {
            key: self.cleaned_data[key]
            for key in SETTING_META
            if key not in self.file_locked_keys and self.cleaned_data[key] != current[key]
        }
