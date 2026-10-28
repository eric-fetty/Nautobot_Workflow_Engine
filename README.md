[![PyPI version](https://img.shields.io/pypi/v/nautobot-workflow-engine.svg)](https://pypi.org/project/nautobot-workflow-engine/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/nautobot-workflow-engine.svg)](https://pypi.org/project/nautobot-workflow-engine/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/nautobot-workflow-engine.svg)](https://pypi.org/project/nautobot-workflow-engine/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/LICENSE)

# Nautobot Workflow Engine

Define automation in YAML, synchronize it through Nautobot's repository integration,
and launch it from dynamic forms. Execute Python scripts in Nautobot or job templates
in AWX, with ordered actions, reusable results, scheduling, and run history.

## Capabilities

- Typed inputs, Nautobot object lookups, dependent choices, conditional visibility,
  validation, and tabular CSV input.
- Pre-actions, main actions, and post-actions in definition order.
- Reusable Python helpers and repository-backed Jinja filters.
- Immediate or scheduled runs, including optional ServiceNow change windows.
- Execution logs, AWX job monitoring, reporting, and estimated time savings.

## Get Started

The current package requires **Nautobot 3.x** and **Python 3.10 through 3.13**. The
selected Nautobot release must also support that Python version. These are package
requirements, not a claim that every combination has been tested.

1. Install from PyPI with `pip install nautobot-workflow-engine`, then follow
   [installation and configuration](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/admin/install.md).
2. Configure a trusted workflow source and synchronize its Workflow Engine content.
3. Run the non-destructive [hostname tutorial](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/user/app_getting_started.md).
4. Use the [authoring reference](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/user/app_use_cases.md) to add inputs and actions.

AWX and ServiceNow are optional. Individual workflows may require additional apps,
Python packages, inventory, or service permissions. Examples are starting points for
review and adaptation, not production-ready deployment definitions.

## Documentation

- [Overview](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/user/app_overview.md) and [documentation home](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/index.md).
- [Workflow authoring](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/user/app_use_cases.md) and [custom filters](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/dev/extending.md).
- [AWX and ServiceNow](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/user/external_interactions.md).
- [Compatibility](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/admin/compatibility_matrix.md), [upgrades](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/admin/upgrade.md),
  and [release notes](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/admin/release_notes/index.md).
- [Security and data handling](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/admin/security.md).

## Examples

The [custom-filter workflow](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/examples/workflows/custom-filter-demo.yaml) uses
[an echo script](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/examples/scripts/echo_hostname.py) and shared helpers without changing
inventory. Other examples illustrate inventory operations and optional vendor
integrations. Review their model, script, and service dependencies before use.

## Trust and Execution Logs

Workflow scripts and filters are trusted code running inside Nautobot, not sandboxed
submissions. Limit who can modify their source and who can launch or inspect runs.
Detailed script execution logging is intentional. Treat inputs, results, output,
diagnostics, and backups as operational data; do not submit or print credentials.

## Contributing

Read the [contribution guide](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/dev/contributing.md),
[development environment guide](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/dev/dev_environment.md), and
[architecture overview](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/docs/dev/arch_decision.md). Keep changes focused, use synthetic
fixtures, preserve migrations, and include regression coverage for behavioral changes.

## Publication Status

This project is licensed under the [Apache License 2.0](https://github.com/eric-fetty/Nautobot_Workflow_Engine/blob/main/LICENSE). Open-source
preparation is otherwise in progress. A private vulnerability-reporting contact and
final artifact validation are still required before public release.