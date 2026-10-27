# App Overview

Workflow Engine turns workflow definitions into launch forms and runs inside Nautobot.
Engineers build them in a visual builder or in YAML, operators run them from a form
without touching code, and every run is recorded.

## Capabilities

- Build workflows in a [visual builder](builder.md): inputs, steps, conditions, schedule and approval, with a live launch form preview, [Preview run](preview_run.md), drafts, versions and one-click publish.
- Publish to Git and import changes from Git, with failures explained in plain words. See [Publishing and versions](publishing.md).
- Write a starter script from the builder that prints every value it receives. See [New scripts from the builder](placeholder_scripts.md).
- Synchronize workflow definitions, Python scripts, helpers, and filters from repositories.
- Collect typed inputs, including Nautobot objects, dependent choices, and tabular data.
- Run ordered pre-actions, main actions, and post-actions with reusable results.
- Launch Nautobot scripts or AWX job templates and monitor their outcomes.
- Schedule runs, optionally using a validated ServiceNow change window.
- Browse run history, execution logs, and time saved on the [dashboard](app_reporting.md).

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
[builder guide](builder.md) and the [YAML reference](app_use_cases.md) for more advanced definitions, and the
[reporting dashboard](app_reporting.md) for usage and time-savings reporting.
