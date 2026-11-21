# v1.4 Release Notes

This document describes all new features and changes in the release. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release Overview

Version 1.4 is a feature release.

- **Run history search.** Nautobot's header search and global search find workflow runs by workflow, user, status, change ticket, form values and picked objects. Device and virtual machine pages show a "Workflow runs" panel, and administrators can add the panel to more object types on the Settings page.
- **Clearer run pages.** Picked objects show by name and link to their pages instead of showing ids, and the User Log and Debug Log panels have copy buttons.
- **Regex validation in the builder.** Text, list and table-column inputs have a Validation panel with ready-made presets and a box to try a value. The server now checks every line of a list input.
- **Docs.** A chart maps the Workflow Engine onto the Network Automation Forum's reference architecture, and the examples tutorial adds three workflows that branch on earlier steps' results, including rollback and fallback.

### Upgrading

- There are no new migrations. Run `nautobot-server post_upgrade` as usual.
- Compatibility is unchanged: Nautobot `>=3.0.0,<4.0.0` and Python `>=3.11,<3.15`.
- A validation pattern that Python can't compile is now rejected when a workflow is saved, published or synced from Git. Before, it only failed on the launch form. Fix any such pattern before syncing.

<!-- towncrier release notes start -->


## v1.4.0 (2026-11-21)

### Added

- #builder-validators - Added a Validation panel to the workflow builder for text, list, and table-column inputs: add, edit, and remove pattern rules with their error text and flags, insert ready-made presets (hostname, IPv4 address, IPv4 network, MAC address, interface name, VLAN name, ServiceNow change number), and try a sample value against the rules as you type.
- #run-history-search - Added run history search: Nautobot's header search on Run History (shown as "in: Run History") and Nautobot's global search match runs by workflow, user, status, change ticket, form values and the names of picked objects. Device and virtual machine pages show a "Workflow runs" panel; administrators pick more object types on the Settings page. The Workflows list uses the header search too ("in: Workflows").
- #run-input-object-links - A run's page shows picked devices, VMs and other objects by name, with their type, linked to their pages, instead of their ids.
- #run-log-copy - Added Nautobot's copy-to-clipboard button to the User Log and Debug Log panels on a run's page.

### Fixed

- #builder-validators - The server now checks every line of a list input against its validation rules, as the launch page already did. A pattern Python can't compile is now a `regex_invalid` validation error when a workflow is saved, published, or synced from Git, instead of an error on the launch form.

### Documentation

- #conditional-examples - Added examples 11–13 to the Examples tutorial: rollback when a step fails, a fallback prefix, and branching on a post-check's return value.
- #naf-image-link - Fixed the README's NAF chart image link, which pointed at a file that isn't on main yet.
- #naf-mapping - Added a diagram and table that map the Workflow Engine onto the Network Automation Forum's NAF reference architecture.
