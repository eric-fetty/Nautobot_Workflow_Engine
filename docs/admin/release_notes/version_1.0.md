# v1.0 Release Notes

## Release Overview

Version 1.0.0 is the initial open-source release of Nautobot Workflow Engine.
It provides repository-synchronized workflow definitions, dynamic input forms,
multi-step execution, and run monitoring inside Nautobot.

<!-- towncrier release notes start -->

## v1.0.0 (Unreleased)

### Workflow Authoring

- Define workflows in YAML and synchronize definitions, scripts, helper modules,
	and custom Jinja filters through Nautobot's repository integration.
- Collect strings, integers, booleans, choices, multiple choices, Nautobot object
	selections, lists, and structured tables with CSV import and template download.
- Configure dependent fields, conditional visibility, and input validation.
- Reuse YAML fragments, Python helpers, and custom filters across workflows.

### Execution and Integrations

- Execute trusted Python scripts in Nautobot or launch AWX job templates.
- Run pre-actions, main actions, and post-actions in their defined order.
- Render action parameters and conditions with Jinja, using inputs and earlier
	action results.
- Monitor AWX jobs and expose their status and links in workflow run details.
- Schedule workflow execution, optionally using validated ServiceNow change windows.
- Cancel scheduled runs before execution begins.

### Operations and Reporting

- Browse workflows by category and disable workflows while retaining run history.
- Inspect run status, submitted inputs, action results, and detailed execution logs.
- Stream script output to run details. Detailed execution logging is an intentional
	feature of the application.
- View execution history, dashboard statistics, and estimated time savings based
	on workflow manual-duration estimates.

### Requirements

- Nautobot `>=3.0.0,<4.0.0`.
- Python `>=3.10,<3.14`, also supported by the selected Nautobot release.
- Working Nautobot background workers and scheduling services.
- A trusted workflow source and any dependencies required by its scripts.

AWX and ServiceNow are optional. Configure their credentials using Nautobot Secrets
Groups when enabling those integrations. See the
[compatibility guide](../compatibility_matrix.md) for qualification of the supported
version ranges and the [integration guide](../../user/external_interactions.md) for
configuration details.

### Important Considerations

Workflow scripts and filters execute trusted Python code; they are not sandboxed.
Inputs, results, and execution logs may contain operational data. Review
[security and data handling](../security.md) before deploying workflows.

The public release series starts at 1.0.0. This version reset does not imply a
database schema reset. Existing deployments must retain migration history and
follow the [upgrade guidance](../upgrade.md).

For a new deployment, start with [installation](../install.md) and the
[first workflow tutorial](../../user/app_getting_started.md).
