# Workflow schema

This page is generated from `nautobot_workflow_engine/schema/workflow.schema.json`.
Do not edit it by hand. Run `invoke generate-schema-docs` after a schema change.

The schema is the only list of input types, column types, action types, and phase aliases.
Unknown keys are kept. `enabled` is operational state and is not part of the Git file.

## Workflow

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `key` | string | Key |  | Stable slug. It does not change after the first publish. |
| `name` | string | Name |  | Name shown on the launch page. |
| `description` | string | Description |  | What this workflow does. Trailing newlines are kept. |
| `category` | string | Category |  | Groups the workflow on the launch page. |
| `approval_required` | boolean | Approval required |  | When true on Nautobot 3.2+, launches can enter the approval pipeline. |
| `manual_duration_minutes` | integer | Manual duration (minutes) |  | Minutes this work would take without automation. |
| `schedule` | ref `schedule` | Schedule |  | When the workflow may run. YAML key: schedule. |
| `inputs` | array | Inputs |  | Fields the person launching the workflow fills in. YAML key: inputs. |
| `pre_actions` | array | Before |  | Steps that run before the main steps. A failure stops the run. YAML key: pre_actions. Aliases: `pre-actions`, `before_actions`, `before-actions` |
| `actions` | array | Main |  | The main steps, in order. YAML key: actions. Aliases: `main_actions`, `main-actions` |
| `post_actions` | array | After |  | Steps that always run last. YAML key: post_actions. Aliases: `post-actions`, `after_actions`, `after-actions` |

## input

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `key` | string | Key |  | Variable name. Refer to it as {{ inputs.<key> }}. YAML key: key. |
| `label` | string | Label |  | Text shown beside the field on the launch form. YAML key: label. |
| `type` | `string`, `integer`, `boolean`, `choice`, `multichoice`, `object`, `multiobject`, `list`, `listdict` | Type |  | Text, whole number, yes/no, choice, Nautobot object, list, or table. YAML key: type. |
| `required` | boolean | Required |  | The person launching the workflow must fill this in. YAML key: required. |
| `default` |  | Default |  | A scalar, list, or object, matching the input type. |
| `help_text` | string | Help text |  | Shown under the field on the launch form. YAML key: help_text. |
| `placeholder` | string | Placeholder |  | Grey hint inside the empty field. YAML key: placeholder. |
| `depends_on` | array | Depends on |  | Other inputs that must have a value first. Select more than one. Clear the selection to remove them. YAML key: depends_on. |
| `show_if` | one of | Show if | yes | When to show this input. Use field and value rows, or a Jinja template such as {{ inputs.confirm }}. Jinja2 is allowed. YAML key: show_if. |
| `choices` | one of | Options | yes | A list of value and label options, or a Jinja template that returns them. Jinja2 is allowed. YAML key: choices. |
| `model` | string | Model |  | Nautobot model, for example dcim.Device. YAML key: model. |
| `filter` | object | Filter |  | Limit which objects are offered. YAML key: filter. |
| `columns` | array | Columns |  | Columns of a table input. YAML key: columns. |
| `validators` | array | Validators |  | Patterns the value must match, each with a message and flags. YAML key: validators. |

## choice

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `value` |  | Value |  | Value stored when this option is picked. YAML key: value. |
| `label` | string | Label |  | Text shown for this option. YAML key: label. |

## column

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `key` | string | Key |  | Column name stored on each row. YAML key: key. |
| `label` | string | Label |  | Column heading. YAML key: label. |
| `type` | `string`, `integer`, `boolean`, `choice` | Type |  | Text, whole number, yes/no, or choice. number is stored as integer. YAML key: type. Aliases: `number` |
| `required` | boolean | Required |  | The row must include this column. YAML key: required. |
| `unique` | boolean | Unique |  | Values in this column cannot repeat. YAML key: unique. |
| `choices` | one of | Options | yes | Options for a choice column, or a Jinja template. Jinja2 is allowed. YAML key: choices. |
| `validators` | array | Validators |  | Patterns a column value must match. YAML key: validators. |

## validator

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `regex` | string | Pattern |  | Regular expression the value must match. YAML key: regex. |
| `message` | string | Message |  | Error shown when the value does not match. YAML key: message. |
| `flags` | array | Flags |  | Regex flags. Case-insensitive ignores letter case. Multi-line, dot matches newline, and verbose are the other options. YAML key: flags. |

## action

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `type` | `nautobot`, `awx` | Type |  | Run a Nautobot script or launch an AWX job template. YAML key: type. |
| `name` | string | Name |  | Step name. Results are available as {{ results.<name> }}. Must be unique. YAML key: name. |
| `when` | string | Condition | yes | Run this step only when the condition is true. Jinja2 is allowed, for example {{ inputs.confirm }}. YAML key: when. |
| `script` | string | Script | yes | Script path under the scripts directory. YAML key: script. |
| `with` | object | Parameters | yes | Parameters passed to the script. Values may be Jinja templates. Jinja2 is allowed. YAML key: with. |
| `template_id` | integer | Job template ID |  | Numeric AWX job template ID. YAML key: template_id. |
| `extra_vars` | object | Extra vars | yes | Variables passed to the AWX job. Values may be Jinja templates. Jinja2 is allowed. YAML key: extra_vars. |
| `monitor` | boolean | Wait for the job |  | Wait for the AWX job to finish. YAML key: monitor. |

## Schedule

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `servicenow` | ref `servicenow` |  |  |  |

## ServiceNow

| Field | Type | Label | Code | Description |
| --- | --- | --- | --- | --- |
| `integration_name` | string | Integration |  | External Integration to use. Defaults to ServiceNow. YAML key: integration_name. |
| `change_input_key` | string | Change ticket input |  | Input that holds the change ticket number. YAML key: change_input_key. |
| `allowed_states` | array | Allowed states |  | Change states that allow the run, for example Scheduled or Implement. YAML key: allowed_states. |
| `timezone` | string | Timezone |  | IANA timezone of the change window, or UTC. YAML key: timezone. |
| `start_fields` | array | Start fields |  | ServiceNow fields to read the window start from, tried in order. YAML key: start_fields. |
| `end_fields` | array | End fields |  | ServiceNow fields to read the window end from, tried in order. YAML key: end_fields. |
| `ticket_regex` | string | Ticket pattern |  | Pattern the ticket number must match, for example CHG\d{7}. YAML key: ticket_regex. |
| `require_approved` | boolean | Require approved |  | Only accept changes that are already approved. YAML key: require_approved. |

## Phase aliases

| Canonical key | Aliases |
| --- | --- |
| `pre_actions` | `pre-actions`, `before_actions`, `before-actions` |
| `actions` | `main_actions`, `main-actions` |
| `post_actions` | `post-actions`, `after_actions`, `after-actions` |
