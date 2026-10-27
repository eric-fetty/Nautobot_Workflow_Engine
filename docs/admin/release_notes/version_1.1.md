# v1.1 Release Notes

This document describes all new features and changes in the release. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release Overview

- Optional Nautobot **3.2+** Approval Workflow integration: set `approval_required` in workflow YAML so git sync maintains a scoped Approval Workflow Definition, and immediate launches that need approval use a near-term scheduled job (`pending_approval`) instead of Celery immediate execution.
- Operators configure approver groups and stages in the Nautobot Approvals UI after sync; the app creates definition shells only.
- Includes related scheduling/UI polish, migration cleanup for an orphaned experimental column, and documentation for authors and administrators.
- Package compatibility remains Nautobot `>=3.0.0,<4.0.0` and Python `>=3.10,<3.14`. The approval feature is a no-op on Nautobot versions below 3.2.
- Workflow Jinja (`when`, `with`, `extra_vars`, choices, and filter queries) now renders in a sandbox. Templates that reach private attributes or call methods that change data are rejected. The patterns the sandbox refuses include `cycler.__init__.__globals__`, `lipsum.__globals__`, `joiner.__init__.__globals__`, `namespace.__init__.__globals__`, and `{{ user.password }}`.
- Workflow definitions now have one JSON Schema. Drafts and published revisions are stored separately, and the builder API rejects a save that started from an older revision. Git sync keeps going when one file is invalid. The `when` column stores the full template, up to the 10 kB cap.
- Nautobot-managed workflows can publish to their own Git repository and import later commits. The later change wins. A failed push leaves the database save in place and shows **Not synced · Retry**. A `number` column keeps decimal values; `integer` rejects them.
- The workflow builder is one editor for every workflow. Changes autosave into a shared draft. **Publish changes** writes the next version, pushes that YAML to Git, and makes it live. **Enabled** is a Nautobot switch and does not commit. The list is a single Workflows page with Run, Edit, and that switch.
- **Preview run** renders conditions and parameters without starting work. **Edit YAML** parses into the draft. Launch forms cover every input type, including table CSV import that stays in the browser.
- Step conditions can test earlier steps (succeeded, failed, was skipped, or their output). See [Conditions](../../user/conditions.md).
- **New script…** in the builder writes a starter script from the step's parameters and commits it to the repository. The starter prints each value it receives to the run's log. It needs the new **Write scripts** permission (`write_script`). The builder only parses the code. See [New scripts from the builder](../../user/placeholder_scripts.md).
- Permissions are simpler. Templates, conditions, script choices and the YAML view are part of **Build workflows**; the separate `author_code` permission is gone. There is no approve permission: approvers come from the stage approver groups in Nautobot's approval workflow definitions. Upgrading deletes the earlier **Author templates and scripts** and **Approve runs** entries if nobody changed or assigned them, and logs a warning for any it keeps. See [Permissions](../../user/permissions.md).
- Publishing explains every failure in plain words, names the fix, and flags a repository that isn't ready before anyone presses Publish. See [When publishing fails](../../user/publishing.md#when-publishing-fails).
- New user guides for the builder, the launch page, inputs and CSV import, steps and Jinja, conditions, schedule and approval, Preview run, publishing and Git, and new scripts, with current screenshots.

<!-- towncrier release notes start -->


## v1.1.2 (2026-10-16)

### Fixed

- #orphan-workflow-sync - Fixed git sync so workflows removed from a repository are disabled on the launch page (orphan disable on every sync, plus get_absolute_url helpers for change-logging).
- #migration-nautobot-300 - Fixed initial migration dependency so Nautobot 3.0.0 can run post_upgrade (depend on extras.0132 instead of extras.0145).

### Documentation

- #apache-license - Added the Apache-2.0 license file and corrected the README license badge.
- #ui-screenshots - Added UI screenshots to end-user and admin documentation for getting started, authoring, reporting, integrations, and install guides.

### Housekeeping

- #ci-url-tests - Fixed CI by sorting migration imports and bypassing Django FK descriptors in get_absolute_url unit tests.

## v1.1.1 (2026-10-15)

### Fixed

- #auto-enable-jobs - Fixed required Workflow Engine jobs remaining disabled after install by auto-enabling the scheduled executor and orphan cleaner on database ready.
- #installed-apps-version - Fixed Installed Apps showing a stale package version by preferring pyproject.toml in source checkouts.

### Documentation

- #servicenow-job-docs - Documented click-by-click steps to enable and schedule the Sync ServiceNow Scheduled Workflows job during setup.

## v1.1.0 (2026-10-15)

### Added

- #approval-required - Added optional Nautobot 3.2+ approval integration via workflow YAML `approval_required`, including scoped Approval Workflow Definition sync and approval-gated immediate launches.

### Fixed

- #4 - Fixed git sync IntegrityError when an older experimental `requires_approval` database column was left behind after renaming the field to `approval_required`.

### Documentation

- #approval-required - Documented Nautobot approval-required workflows for authors, administrators, and operators, including stage configuration and troubleshooting.

### Housekeeping

- Replaced the stub GitHub Actions workflow that called missing `pytest` with Docker-based `invoke unittest` and ruff checks matching the local development workflow.
