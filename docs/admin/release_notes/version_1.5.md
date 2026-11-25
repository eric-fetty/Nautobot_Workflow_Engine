# v1.5 Release Notes

This document describes all new features and changes in the release. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release Overview

Version 1.5 is a feature release.

- **Workflow tags.** Workflows use Nautobot tags. Pick them in the builder's Details step, see them as coloured chips on the Workflows list, filter the list by tag, and add or remove tags on many workflows at once with **Bulk edit tags**. Tags stay in Nautobot and are never written to the workflow YAML, so a Git sync keeps them. Duplicating a workflow copies its tags.
- **Change freezes.** Under **Workflows → Change Freezes**, set one-time or recurring windows (weekend, nightly, month-end and holiday presets, or your own RRULE) for all workflows or by category, tag or workflow, with exclusions. During a freeze nothing in a covered workflow runs, not even a scheduled run's Before steps: launching and scheduling are refused, and a run that comes due is marked the new **Blocked by freeze** status. Banners and lock badges show freezes to anyone who can see the workflow, with a "starts soon" warning (2 hours by default, set per freeze).
- **Overrides.** People with **Override change freezes** can run anyway with a reason that is saved on the run. The same permission is needed for a tag or category change that would take a workflow out of a freeze; Bulk edit skips such workflows and Git sync leaves them unchanged with `E-NWE-302`.
- **REST API** for change freezes, with status and preview endpoints. See [Change Freezes](../change_freezes.md).

### Upgrading

- There are three new migrations: `0017_workflow_nautobot_tags` (switches workflow tags to Nautobot tags), `0018_changefreeze` and `0019_workflowrun_freeze_fields`. Run `nautobot-server post_upgrade` as usual.
- The upgrade creates three ready-made permissions, unassigned: **Workflow Engine: View change freezes**, **Workflow Engine: Manage change freezes** and **Workflow Engine: Override change freezes**. Assign them to users or groups under **Admin → Users → Permissions**; until then only superusers can manage or override freezes. See [Permissions](../../user/permissions.md#change-freezes).
- To use a tag on workflows, add **Workflow Engine | workflow** to the tag's content types.
- Compatibility is unchanged: Nautobot `>=3.0.0,<4.0.0` and Python `>=3.11,<3.15`.

<!-- towncrier release notes start -->


## v1.5.0 (2026-11-25)

### Added

- #change-freezes - Change freezes: under **Workflows → Change Freezes**, set one-time or recurring windows (RRULE, length and time zone, with weekend, nightly, month-end and holiday presets) for all workflows or by category, tag or workflow, with exclusions. During a freeze nothing in a covered workflow runs: launching and scheduling are refused, and a run that comes due is marked the new **Blocked by freeze** status. Banners and lock badges show freezes to anyone who can see the workflow, with a per-freeze "starts soon" warning (2 hours by default). **Override change freezes** lets someone run anyway with a reason stamped on the run, and is also needed for a tag or category change that would take a workflow out of a freeze; Git sync skips such a change with `E-NWE-302`. Includes a REST API with status and preview endpoints, and three new ready-made permissions.
- #workflow-tags - Workflows now use Nautobot tags: pick them in the builder's Details step, see them as coloured chips on the Workflows list, filter the list by tag, and add or remove tags on many workflows at once with Bulk edit. Tags stay in Nautobot and are never written to the workflow YAML, so a Git sync keeps them.

### Changed

- #change-freezes - Duplicating a workflow now copies its tags.

### Fixed

- #workflow-tags - Saving tags from the builder or the API no longer fails: `Workflow.tags` was a plain django-taggit field that rejected Nautobot tags. The API now returns a 400 naming any unknown tag, or one not allowed on workflows, instead of dropping it.

### Documentation

- #change-freezes - Added a Change Freezes page to the Administrator Guide, a "During a change freeze" section to Running a Workflow, the freeze permissions to Permissions, and two FAQ entries.
- #workflow-tags - Documented workflow tags and tag bulk edit in the builder guide, with a FAQ entry on why tags are not in the YAML.
