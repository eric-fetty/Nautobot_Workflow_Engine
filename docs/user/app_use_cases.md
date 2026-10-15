# Workflow Authoring Reference

This document describes common use-cases and scenarios for this App.

## General Usage

The Workflow Engine lets you define multi-step workflows in YAML files stored in a Git repository. Once synced, each workflow generates a dynamic form in the Nautobot UI where users provide inputs, and the plugin executes the configured actions (Nautobot scripts or AWX jobs) using those inputs.

## Defining Workflows

Workflows are defined as YAML files under `/workflows/` in a Git repository configured in Nautobot's **Git Repositories** admin page. Each file contains a single workflow definition.

### Basic Workflow Structure

```yaml
key: unique-identifier        # URL-safe slug, must be unique
name: "Display Name"          # Shown in the UI
category: "Grouping Category" # Groups workflows in the listing page
description: "What this workflow does"
manual_duration_minutes: 30   # Estimated time to do manually (for ROI tracking)
enabled: true                 # Set to false to disable without deleting
approval_required: false      # Optional; Nautobot 3.2+ approval integration (default: false)

inputs:
  - key: field_key
    label: "Field Label"
    type: string
    required: true

actions:
  - type: nautobot
    script: "scripts/my_script.py"
    with:
      param: "{{ inputs.field_key }}"
```

## Requiring Nautobot Approval (3.2+)

On Nautobot 3.2 and later, set `approval_required: true` on a workflow to integrate with
Nautobot's [Approval Workflows](https://docs.nautobot.com/projects/core/en/stable/user-guide/platform-functionality/approval-workflow/).

When the Git repository syncs:

1. The app creates (or updates) an **Approval Workflow Definition** named
   `Workflow Engine: <workflow.key>`.
2. The definition is scoped with constraints so it only matches scheduled jobs for that
   workflow (`job_model__job_class_name` and `kwargs__workflow_key`).
3. Setting `approval_required: false`, disabling the workflow, or removing it from the
   repository deletes that definition when it is safe to do so.

The synced definition is a shell only. After the first sync with the flag enabled,
operators must open **Approvals → Workflow Definitions**, open the matching definition,
and add at least one **stage** (approver group, minimum approvers, sequence). Without
stages, approval requests stay pending indefinitely.

### Launch behavior

| Execution mode | With `approval_required: true` on Nautobot 3.2+ | Without approval (or older Nautobot) |
| --- | --- | --- |
| Execute Immediately | Creates a near-term one-off scheduled job so the run enters the approval pipeline. Status shows **Pending Approval** until approved. | Runs via the normal Celery immediate path. |
| Schedule for Later / ServiceNow | Already creates a `ScheduledJob`; the scoped definition applies automatically. | Unchanged. |

After approval, Nautobot enables the scheduled job and the hidden **Execute Scheduled Workflow**
job runs the workflow. Approvers use **Approvals → Approval Dashboard**. Denying or canceling
approval should be done from the Approvals UI; you can also cancel a **Pending Approval** or
**Scheduled** run from the workflow run detail page when you have permission.

Scheduled jobs created for deferred or approval-gated runs use a compact display name so they
are easy to recognize on the home page and Approval Dashboard:

`{Workflow name} · {requester} · {change ticket or schedule time}`

For ServiceNow-backed schedules, the third segment is the change ticket from the input named by
`schedule.servicenow.change_input_key` (for example `change_ticket`). Otherwise it is the
scheduled time in UTC (for example `Oct 16 14:00 UTC`). Examples:

- `Privileged Change · eric · CHG0001234`
- `Privileged Change · eric · Oct 16 14:00 UTC`

On Nautobot versions below 3.2 the `approval_required` flag is stored but ignored (sync logs
a skip message). Scheduled and immediate launches keep their existing behavior.

```yaml
key: privileged-change
name: "Privileged Change"
enabled: true
approval_required: true
inputs:
  - key: device
    label: Device
    type: object
    model: dcim.Device
    required: true
actions:
  - type: nautobot
    script: "scripts/echo_hostname.py"
    with:
      device_id: "{{ inputs.device.id }}"
```

## Workflow Input Types

The following input types are available for building dynamic forms. Each input is defined in the `inputs:` section of the workflow YAML.

### String

A simple text input field.

```yaml
- key: hostname
  label: Hostname
  type: string
  required: true
  default: "switch-01"
  validators:
    - regex: "^[a-zA-Z0-9-]+$"
      message: "Hostname must contain only letters, numbers, and hyphens."
```

### Integer

A numeric input field.

```yaml
- key: vlan_id
  label: VLAN ID
  type: integer
  required: true
  default: 100
```

### Boolean

A checkbox for true/false values.

```yaml
- key: enable_dhcp
  label: Enable DHCP
  type: boolean
  default: true
```

### Choice

A single-selection dropdown.

```yaml
- key: request_type
  label: Request Type
  type: choice
  required: true
  choices:
    - { value: permanent, label: "Permanent New Installation" }
    - { value: reservation, label: "90-Day Reservation" }
    - { value: move, label: "Move Existing Equipment" }
    - { value: decommission, label: "Decommission Existing Equipment" }
```

### Multi-Choice

A multi-selection dropdown allowing the user to select one or more values from a predefined list.

```yaml
- key: tags
  label: Tags
  type: multichoice
  choices:
    - { value: production, label: "Production" }
    - { value: staging, label: "Staging" }
    - { value: development, label: "Development" }
```

### Object

A searchable dropdown bound to a Nautobot model. Returns the selected object's primary key.

```yaml
- key: device
  label: Device
  type: object
  model: dcim.Device
  required: true
  depends_on: ["location"]
  filter:
    query: {"location_id": "{{ inputs.location.id }}"}
```

### Multi-Object

Like `object`, but allows selecting multiple Nautobot model instances.

```yaml
- key: devices
  label: Devices
  type: multiobject
  model: dcim.Device
  required: true
  depends_on: ["location"]
  filter:
    query: {"location_id": "{{ inputs.location.id }}"}
```

### List

A dynamic list of string values. Users can add and remove items.

```yaml
- key: dns_servers
  label: DNS Servers
  type: list
  required: true
  validators:
    - regex: "^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}$"
      message: "Must be a valid IPv4 address."
```

### List of Dictionaries (listdict)

A dynamic table where each row represents a dictionary with predefined columns. Users can add and remove rows, import rows from CSV, download a matching CSV template, and each cell is validated according to its column definition. This is ideal for collecting structured, tabular data such as endpoint lists, user rosters, or port mappings.

```yaml
- key: endpoints
  label: Endpoints
  type: listdict
  required: true
  columns:
    - key: name
      label: Endpoint Name
      type: string
      required: true
      unique: true
      validators:
        - regex: "^.+$"
          message: "Endpoint name cannot be empty."
    - key: ip_address
      label: IP Address
      type: string
      required: true
      validators:
        - regex: '^(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:\/(3[0-2]|[12]?[0-9]))?$'
          message: "Enter IPv4 notation with optional CIDR (e.g., 192.0.2.1 or 192.0.2.1/32)."
    - key: enabled
      label: Enabled
      type: boolean
```

#### Column Properties

Each column in a `listdict` input supports the following properties:

| Property     | Type    | Required | Description                                                  |
| ------------ | ------- | -------- | ------------------------------------------------------------ |
| `key`        | string  | Yes      | Unique identifier for the column (used in script context).   |
| `label`      | string  | No       | Display label shown in the table header. Defaults to `key`.  |
| `type`       | string  | No       | Column data type: `string` (default), `integer`, `boolean`, or `choice`. |
| `required`   | boolean | No       | Whether the cell must have a value. Default `false`.         |
| `unique`     | boolean | No       | Whether values in this column must be unique across all rows. Duplicates are highlighted visually. Default `false`. |
| `choices`    | list    | No       | For `choice` columns only. List of `{value, label}` objects. |
| `validators` | list    | No       | List of `{regex, message}` objects for pattern validation.   |

#### Column Types

- **`string`** — Renders a text input. Supports `validators` for regex validation.
- **`integer`** — Renders a numeric input. Validates that the value is a number.
- **`boolean`** — Renders a checkbox. Any boolean value is accepted; `required` has no effect.
- **`choice`** — Renders a dropdown. Requires a `choices` list with `{value, label}` items.

#### How It Works

The `listdict` field renders as a table in the workflow form:

- **Column headers** are derived from each column's `label` (or `key` if no label is specified).
- **Add Row** appends a new empty row to the table.
- **Download Template** generates a CSV file with headers matching the configured columns.
- **Upload CSV** parses a CSV file into the table so the user can review and edit rows before submission.
- **Remove** deletes a specific row.
- Cells with validation errors are highlighted in red with a tooltip describing the error.
- Columns marked `unique: true` show a warning banner when duplicate values are detected.
- CSV headers are matched against the column `key` first and then the column `label`.
- Choice columns accept either the configured choice `value` or the human-readable choice `label` from the CSV.
- Boolean columns accept `true/false`, `yes/no`, `on/off`, and `1/0` in CSV uploads.

Example CSV for the `endpoints` schema above:

```csv
Endpoint Name,IP Address,Enabled
fw-edge-01,192.0.2.1/32,true
fw-edge-02,192.0.2.2/32,false
```

The submitted data is a JSON array of objects, where each object's keys correspond to the column `key` values. For example:

```json
[
  {"name": "fw-edge-01", "ip_address": "192.0.2.1/32", "enabled": true},
  {"name": "fw-edge-02", "ip_address": "192.0.2.2/32", "enabled": false}
]
```

Scripts can access this data via Jinja: `{{ inputs.endpoints }}`, and iterate over it in Python scripts as a standard list of dictionaries.

## Conditional Visibility

Fields can be shown or hidden based on the value of another field using `show_if`:

```yaml
- key: request_type
  label: Request Type
  type: choice
  choices:
    - { value: new, label: "New Installation" }
    - { value: decommission, label: "Decommission" }

- key: device_name
  label: Device Name
  type: string
  required: true
  show_if:
    request_type: new
```

In this example, `device_name` is only shown when the user selects "New Installation".

## Cascading Dependencies

Object and choice fields can depend on other fields using `depends_on` and Jinja-templated `filter.query` values:

```yaml
- key: location
  label: Location
  type: object
  model: dcim.Location

- key: device
  label: Device
  type: object
  model: dcim.Device
  depends_on: ["location"]
  filter:
    query: {"location_id": "{{ inputs.location.id }}"}
```

When the user selects a location, the device dropdown is filtered to that location.
The object examples above assume that this parent `location` input is also defined.

## Input Validation

All text-based input types (`string`, `list`, and `listdict` columns) support regex validation:

```yaml
validators:
  - regex: "^[A-Za-z0-9_-]+$"
    message: "Only letters, numbers, underscores, and hyphens are allowed."
    flags: ["IGNORECASE"]  # Optional regex flags
```

Validation is performed client-side in real time and again at form submission.

## Workflow Actions

Actions define what happens when the workflow is executed. Each action runs in sequence.
Named results are available through `results`, for example
`{{ results.step_name.return_value }}` for a Nautobot script's return value.

Workflow action order is preserved exactly as defined in the YAML file. That applies independently within `pre_actions`, `actions`, and `post_actions`, so later steps in a phase can safely depend on earlier results.

### Nautobot Script Action

Executes a Python script stored in the Git repository:

```yaml
actions:
  - type: nautobot
    name: "Configure Device"
    script: "scripts/configure_device.py"
    with:
      device: "{{ inputs.device }}"
      vlan_id: "{{ inputs.vlan_id }}"
```

### AWX Job Action

Launches a job template on an AWX/Ansible Tower instance:

```yaml
actions:
  - type: awx
    name: "Deploy Configuration"
    template_id: 123
    extra_vars:
      device_name: "{{ inputs.device.name }}"
```

When multiple actions exist in the same phase, define them in dependency order. For example, if `verify_snow_change` depends on a change ticket created earlier in the workflow, keep the ticket creation action above the verification action in that phase.

## Custom Jinja Filters

Custom Jinja filters are loaded from Python modules under the repository's top-level `filters/` directory by default. Each module must expose a `FILTERS` dictionary that maps the filter name used in YAML to a callable.

```python
# filters/string_filters.py
from scripts.lib.text_helpers import normalize_hostname


def normalize_for_dns(value):
    """Normalize a user-provided hostname for DNS-safe usage."""
    return normalize_hostname(value).replace("_", "-")


FILTERS = {
    "normalize_for_dns": normalize_for_dns,
}
```

Once synced, these filters are available anywhere the app renders Jinja:

- Action `with` parameters
- Action `when` expressions
- Input `filter.query` values
- Dynamic choice definitions

Example usage in workflow YAML:

```yaml
actions:
  - type: nautobot
    name: "Prepare Hostname"
    script: "scripts/create_device.py"
    with:
      hostname: "{{ inputs.hostname | normalize_for_dns }}"
```

### Importing Shared Modules from `scripts/lib`

Filter modules can import repo-local helper code that ships in the same workflow repository. When shared code lives under `scripts/lib`, use normal absolute Python imports that match the materialized package layout:

```python
from scripts.lib.text_helpers import normalize_hostname
```

Avoid relying on plugin-internal modules for custom filters. Instead, place reusable code in the workflow repository and import it from there.

### Dependency Rules

- Python standard-library modules are supported.
- Repo-local helper modules are supported, including shared modules under `scripts/lib`.
- Third-party packages must already be installed in the Nautobot runtime. If a filter imports a package that is missing, Git sync fails early with an import error.

## Action Phases and Results

- `pre_actions` run before the main phase; for scheduled runs they execute when the
  schedule is created. A pre-action failure blocks execution.
- `actions` contains the main work, in definition order.
- `post_actions` run after the main phase, including when main actions fail. A
  post-action failure is recorded without replacing the main result.
- `when` — an optional conditional expression on an action; the action runs only
  when it evaluates to true, for example only when a previous action succeeded.

Give actions distinct names when later steps refer to their results. Result records
include `status` and `return_value`; failed records may include `error`. `last` holds
the previous action's result and name. Use a condition to guard access to a return
value that may not exist after a failure or skipped action:

```yaml
actions:
  - type: nautobot
    name: prepare_hostname
    script: scripts/echo_hostname.py
    with:
      hostname: "{{ inputs.hostname }}"
  - type: nautobot
    name: reuse_hostname
    script: scripts/echo_hostname.py
    when: "{{ results.prepare_hostname.status == 'success' }}"
    with:
      hostname: "{{ results.prepare_hostname.return_value.hostname }}"
```

## Script Contract

Action scripts define `run(params)`, accepting the dictionary rendered from `with`.
They may return data for subsequent actions. Use the
[first workflow](app_getting_started.md) as a complete non-destructive example.
Helpers under `scripts/lib/` can be imported with absolute imports such as
`from scripts.lib.text_helpers import normalize_hostname`.

Scripts execute as trusted Python code in the Nautobot worker environment. Execution
parameters, results, diagnostics, and stdout/stderr are intentionally available in
run logs. See [data handling](../admin/security.md) before using operational data.

## Reusable Inputs and Dynamic Choices

YAML can include shared fragments using `{% include "templates/inputs.yaml" %}`.
Include paths are resolved during synchronization relative to the workflow source;
fragments must preserve the indentation expected at the inclusion point.

Choice definitions can render a JSON list using Jinja and dependent inputs. Use
`depends_on` for the inputs needed to render those choices. For example, a rack and
device type can provide available rack-unit choices using the built-in
`available_rack_units` filter. Custom filters follow the same dependency rules as
other repo-backed Python modules; see [extending the app](../dev/extending.md).

## Scheduling and Workflow Lifecycle

Operators can select one of three execution modes when launching a workflow:

- **Immediate** — the run starts as soon as the form is submitted.
- **Scheduled** — the run is created now and executes at a chosen future time.
- **ServiceNow change window** — the run is scheduled from a validated ServiceNow
  change ticket, and the [sync job](external_interactions.md#keeping-scheduled-runs-in-sync-with-servicenow)
  keeps it aligned with the ticket.

For a scheduled run, `pre_actions` execute when the schedule is created, *before*
the run is finally scheduled to its target time. A pre-action failure blocks the
run. Scheduled runs can be canceled before they start; running workflows cannot be
interrupted through the cancel control. Check [the FAQ](faq.md) for cancellation
permissions.

### Run Lifecycle

A run moves through these statuses as it progresses:

| Status | Meaning |
| --- | --- |
| `scheduled` | Created for a future execution time; not yet running. |
| `pending` | Queued and waiting to be picked up for execution. |
| `running` | Executing its actions now. |
| `success` | All required actions completed successfully. |
| `failed` | One or more required actions failed. |
| `partial` | Some actions completed and some failed. |
| `canceled` | Canceled before or while scheduled. |

The run detail view shows **live progress** while a run is running: a percentage
and status label, AWX job completion (how many of its AWX jobs are done), and
elapsed time. The terminal statuses (`success`, `failed`, `partial`, `canceled`)
mark the end of the run.

Set `enabled: false` to disable a workflow without discarding its run history.
When source removal disables a workflow during synchronization, retain the stored
history for auditing. The `show_disabled` app setting controls list visibility.
Use categories for organization and `manual_duration_minutes` for estimated
[time savings](app_reporting.md#time-savings), not as a measurement of actual
execution duration.

## REST API

The app exposes workflow definitions through a Nautobot REST API viewset, available
under the app's API namespace. Use it to list and read workflow metadata
programmatically, for example to seed an external automation tool. The REST API
honors the same `view_workflow` object permissions as the UI, so a client only
sees the workflows its account can view. See
[granting permissions](../admin/install.md#permissions).
