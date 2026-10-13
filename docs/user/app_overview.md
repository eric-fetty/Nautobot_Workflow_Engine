# App Overview

Workflow Engine turns YAML definitions into forms and executable workflows inside
Nautobot. Operators can run approved automation without editing its source; workflow
authors control inputs, validation, execution order, and integrations.

## Capabilities

- Synchronize workflow definitions, Python scripts, helpers, and filters from repositories.
- Collect typed inputs, including Nautobot objects, dependent choices, and tabular data.
- Run ordered pre-actions, main actions, and post-actions with reusable results.
- Launch Nautobot scripts or AWX job templates and monitor their outcomes.
- Schedule runs, optionally using a validated ServiceNow change window.
- Browse run history, execution logs, and estimated time savings.

## Execution Model

Nautobot repository synchronization stores workflow definitions and scripts in app
models. Launching a workflow validates the form and creates a run. Background workers
execute the actions and record status, inputs, results, and logs. AWX runs are monitored
by background tasks; ServiceNow is consulted only for workflows using its change windows.

Workflow execution logging is intentional and is part of the operator experience.
Workflow authors and administrators must decide which data is appropriate to submit
and print. See [security and data handling](../admin/security.md).

## Prerequisites and Next Steps

A working Nautobot deployment, its background workers, and access to a trusted workflow
repository are required. AWX and ServiceNow are optional. Individual workflows may need
additional apps, Python dependencies, permissions, or remote services.

Start with [installation](../admin/install.md), then follow the
[first workflow tutorial](app_getting_started.md). See the
[authoring reference](app_use_cases.md) for more advanced definitions.
