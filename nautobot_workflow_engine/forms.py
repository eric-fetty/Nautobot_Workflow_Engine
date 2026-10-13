"""Dynamic form generation for workflow inputs."""

from datetime import datetime, timedelta, timezone as dt_timezone
import json
import logging
import re


from django import forms
from django.utils import timezone

from .models import Workflow, WorkflowInput
from .servicenow import normalize_change_ticket


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
                import json

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

    def __init__(self, workflow: Workflow, *args, **kwargs):
        """Initialize dynamic form with fields based on workflow inputs."""
        super().__init__(*args, **kwargs)
        self.workflow = workflow
        for wi in workflow.inputs.all():
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
                        import json

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
                field.required = False
                if wi.visible_when:
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

                        import json

                        field.widget.attrs["data-show-if-conditions"] = json.dumps(additional_conditions)
                # Hidden by default if depends_on, revealed by JS
                if wi.depends_on:
                    # Only set data-depends-on if not already set during field creation
                    if "data-depends-on" not in field.widget.attrs:
                        field.widget.attrs["data-depends-on"] = ",".join(wi.depends_on)
                self.fields[wi.key] = field

        # Add scheduling fields to every workflow form
        execution_help_text = None
        if self.workflow.servicenow_change_input_key:
            execution_help_text = (
                "Select ServiceNow Change Window to validate the configured change ticket and schedule "
                "the workflow from that window."
            )

        EXECUTION_CHOICES = [("immediate", "Execute Immediately"), ("scheduled", "Schedule for Later")]
        if self.workflow.servicenow_change_input_key:
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
            if wi.visible_when:
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
                # Client-side validation handles required fields - skip server-side validation
                pass
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
                        logger.warning("Invalid ServiceNow ticket_regex on workflow %s", self.workflow.pk)
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

        if column_type in ("integer", "number"):
            if value in (None, ""):
                return "", None
            try:
                return int(value), None
            except (TypeError, ValueError):
                return value, f"{column.get('label') or column.get('key') or 'Value'} must be a number."

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

    def _has_jinja_choices(self, wi):
        """Check if workflow input choices contain Jinja template syntax."""
        if not wi.choices:
            return False

        choices_str = str(wi.choices)
        return "{{" in choices_str and "}}" in choices_str

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
                import json

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
                import json

                field.widget.attrs["data-validators"] = json.dumps(wi.validators)

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
                    field.widget.attrs["data-depends-on"] = ",".join(wi.depends_on)
                if wi.input_type == "multichoice":
                    field.widget.attrs["data-multichoice"] = "true"
                    # For static multichoice without dependencies, populate choices now
                    if not has_jinja and not has_dependencies and wi.choices:
                        choices = [(c["value"], c.get("label", c["value"])) for c in wi.choices]
                        field.choices = [("", "— Select —")] + choices
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
                field.widget.attrs["data-depends-on"] = ",".join(wi.depends_on)
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
                field.widget.attrs["data-depends-on"] = ",".join(wi.depends_on)
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
                import json

                field.widget.attrs["data-validators"] = json.dumps(wi.validators)

            # Set initial value if provided (should be a list)
            if wi.default:
                if isinstance(wi.default, list):
                    import json

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
                    "style": "display: none;",
                }
            )

            # Attach columns schema for client-side rendering
            try:
                import json

                if wi.columns is not None:
                    field.widget.attrs["data-columns"] = json.dumps(wi.columns)
            except Exception:  # noqa: S110
                logging.exception("Failed to serialize columns for workflow input %s", wi.key)

            # Set initial value if provided (should be a list of dicts)
            if wi.default:
                if isinstance(wi.default, list):
                    import json

                    field.initial = json.dumps(wi.default)
                else:
                    field.initial = "[]"
            else:
                field.initial = "[]"

            return field
        return None
