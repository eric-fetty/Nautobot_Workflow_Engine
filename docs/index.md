# Nautobot Workflow Engine

Define workflows in YAML, synchronize them through Nautobot's repository integration,
and launch them from dependency-aware forms. Actions run Python scripts in Nautobot
or job templates in AWX. Runs retain inputs, results, execution logs, and status.

## Start Here

- [Overview](user/app_overview.md): capabilities and execution model.
- [Getting started](user/app_getting_started.md): a non-destructive first workflow.
- [Installation and configuration](admin/install.md): prerequisites and app settings.

## Use and Extend Workflows

- [Workflow authoring reference](user/app_use_cases.md): inputs, validation, actions, the UI builder, and examples.
- [Integrations](user/external_interactions.md): AWX and ServiceNow.
- [Custom filters](dev/extending.md): reusable Python helpers and Jinja filters.
- [Frequently asked questions](user/faq.md): scheduling and cancellation.

## Operate and Contribute

- [Compatibility](admin/compatibility_matrix.md) and [upgrades](admin/upgrade.md).
- [Security and data handling](admin/security.md).
- [Release notes](admin/release_notes/index.md).
- [Contributing](dev/contributing.md) and [development environment](dev/dev_environment.md).

The current package requires Nautobot 3.x and Python 3.10 through 3.13. Workflow
scripts and custom filters are trusted code, not a sandbox for untrusted submissions.
