[![PyPI version](https://img.shields.io/pypi/v/nautobot-workflow-engine.svg)](https://pypi.org/project/nautobot-workflow-engine/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/nautobot-workflow-engine.svg)](https://pypi.org/project/nautobot-workflow-engine/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/nautobot-workflow-engine.svg)](https://pypi.org/project/nautobot-workflow-engine/)
[![License](https://img.shields.io/github/license/eric-fetty/Nautobot_Workflow_Engine.svg)](LICENSE)

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

1. Follow [installation and configuration](docs/admin/install.md) using an approved
   package in your Nautobot deployment.
2. Configure a trusted workflow source and synchronize its Workflow Engine content.
3. Run the non-destructive [hostname tutorial](docs/user/app_getting_started.md).
4. Use the [authoring reference](docs/user/app_use_cases.md) to add inputs and actions.

AWX and ServiceNow are optional. Individual workflows may require additional apps,
Python packages, inventory, or service permissions. Examples are starting points for
review and adaptation, not production-ready deployment definitions.

## Documentation

- [Overview](docs/user/app_overview.md) and [documentation home](docs/index.md).
- [Workflow authoring](docs/user/app_use_cases.md) and [custom filters](docs/dev/extending.md).
- [AWX and ServiceNow](docs/user/external_interactions.md).
- [Compatibility](docs/admin/compatibility_matrix.md), [upgrades](docs/admin/upgrade.md),
  and [release notes](docs/admin/release_notes/index.md).
- [Security and data handling](docs/admin/security.md).

## Examples

The [custom-filter workflow](examples/workflows/custom-filter-demo.yaml) uses
[an echo script](examples/scripts/echo_hostname.py) and shared helpers without changing
inventory. Other examples illustrate inventory operations and optional vendor
integrations. Review their model, script, and service dependencies before use.

## Trust and Execution Logs

Workflow scripts and filters are trusted code running inside Nautobot, not sandboxed
submissions. Limit who can modify their source and who can launch or inspect runs.
Detailed script execution logging is intentional. Treat inputs, results, output,
diagnostics, and backups as operational data; do not submit or print credentials.

## Contributing

Read the [contribution guide](docs/dev/contributing.md),
[development environment guide](docs/dev/dev_environment.md), and
[architecture overview](docs/dev/arch_decision.md). Keep changes focused, use synthetic
fixtures, preserve migrations, and include regression coverage for behavioral changes.

## Publication Status

Open-source preparation is in progress. Apache-2.0 is the intended license, subject
to copyright-owner approval and a provenance review. No project-wide license grant
is asserted here until those steps are complete. A private vulnerability-reporting
contact and final artifact validation are also required before public release.