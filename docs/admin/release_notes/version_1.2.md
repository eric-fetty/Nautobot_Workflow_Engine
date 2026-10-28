# v1.2 Release Notes

This document describes all new features and changes in the release. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release Overview

Version 1.2 turns the workflow engine into a self-service builder that people use from the Nautobot UI.

- **Visual builder.** Build a workflow's inputs, steps, conditions, schedule and approval in one editor with an autosaved shared draft, a YAML view that stays in step, and **Preview run**, which renders everything without running anything. See [The workflow builder](../../user/builder.md).
- **New launch page.** It covers every input type, including tables with CSV import, and shows what will happen before you run. See [Running a workflow](../../user/launch.md).
- **Publishing to Git.** **Publish changes** writes the next version, pushes its YAML to the workflow's repository and makes it live. Later Git commits are imported back. Failures are explained in plain words with the fix. See [Publishing and Git](../../user/publishing.md).
- **New scripts from the builder.** **New script…** commits a starter script that prints each parameter to the run's log. See [New scripts from the builder](../../user/placeholder_scripts.md).
- **Settings page.** Admins change every app setting from the gear icon under **Installed Apps**. See [Install and configure](../install.md#settings-page).
- **Ready-made, simpler permissions.** Install and upgrade create five unassigned permissions: Run workflows, View runs, Build workflows, Publish workflows and Write scripts. Approvers come from Nautobot's approval workflow definitions, not a permission. See [Permissions](../../user/permissions.md).
- **Sandboxed Jinja.** Workflow templates render in a sandbox that blocks private attributes and methods that change data.

### Upgrading

- New migrations run with `nautobot-server post_upgrade`. They create the ready-made permissions and, for installs that had earlier development builds, fold the retired permissions into the new ones. Anything an admin changed or assigned is left in place with a warning in the log. See [Upgrade](../upgrade.md).
- Compatibility is unchanged: Nautobot `>=3.0.0,<4.0.0` and Python `>=3.10,<3.14`. Approval workflows need Nautobot 3.2 or later and are skipped on older versions.

<!-- towncrier release notes start -->


## v1.2.1 (2026-10-27)

### Fixed

- #pypi-readme-links - Fixed broken links in the PyPI project description and added project URLs.

## v1.2.0 (2026-10-27)

### Security

- #jinja-sandbox - Sandboxed workflow Jinja in conditions, parameters, choices, and filter queries. The sandbox rejects private-attribute escapes such as cycler.__init__.__globals__, lipsum.__globals__, joiner.__init__.__globals__, and namespace.__init__.__globals__, and it blocks methods that change data. user.password is no longer available to a template.

### Added

- #default-permissions - Installing or upgrading the app now creates ready-made object permissions, assigned to nobody: Run workflows (workflow: view, run, cancel), View runs (workflow run: view), Build workflows (workflow: view, add, change), Publish workflows (workflow: publish), and Write scripts (workflow: write_script). An admin only adds users or groups, and existing permissions with those names are never changed.
- #git-publishing - Added publishing to Git for workflows built in Nautobot. Publish changes writes the next version, pushes its YAML to the workflow's repository, and makes it live, and later commits in Git are imported back (the later change wins). Any repository that provides workflows and has an HTTP(S) token can be published to. A failed push keeps the save and shows Not synced · Retry. Publish errors say why in plain words and name the fix, and a repository that isn't ready is flagged before anyone presses Publish.
- #launch-page - Added a new launch page that covers every input type, including tables with CSV import checked in the browser and again on the server. It also has a What happens panel that lists the steps in plain words and shows any approval or change window.
- #placeholder-scripts - Added New script… to the builder. It writes a starter script from the step's parameters in the existing PARAMETERS and run(params) format and commits it to the repository's scripts folder. The starter prints each parameter's name and value to the run's log under a "# Replace this with your logic." comment. The builder only parses the code and never runs it.
- #settings-page - Added a Settings page, opened from the gear icon next to Workflow Engine under Installed Apps, where admins change every app setting, including the scripts folder and the Git publishing settings. Settings pinned in nautobot_config.py still win and show as locked, and the old config page redirects there.
- #visual-builder - Added a visual workflow builder with Details, Inputs, Steps, Schedule & approval, and Review & publish sections. Changes autosave into one shared draft. Steps take parameters from an input, a fixed value, or an expression in a shared Jinja editor, and step conditions can test earlier steps (succeeded, failed, was skipped, or their output). A YAML view always shows the same draft, and Versions, Duplicate, and Download YAML are in the menu. Preview run resolves inputs, evaluates conditions, and renders parameters with skip reasons, and never runs a script or AWX job.
- #workflow-schema - Added one JSON Schema for workflow definitions, separate draft and published revisions, and a builder REST API that rejects a save started from an older revision.

### Changed

- #one-provider - Publishing uses the existing "workflows" repository provider. The separate "Workflow publishing (Nautobot → Git)" provider is removed, and the upgrade drops it from repositories that had it ticked, which keep publishing.
- #permission-consolidation - Simplified permissions. Templates, conditions, script choices, and the YAML view are part of Build workflows. There is no approve permission, because approvers come from the stage approver groups in Nautobot's approval workflow definitions. Run workflows now covers seeing, running, and cancelling a workflow's Scheduled or Pending Approval runs. Upgrading updates an unchanged Run workflows in place and keeps its assignments. It deletes the earlier Author templates and scripts, Approve runs, View workflows, and Cancel runs entries only when they are unchanged and unassigned, and logs a warning for anything it leaves. Permissions that use add_workflowrun and change_workflowrun keep working.
- #workflows-list - Replaced the workflow pages with one Workflows list showing Run, Edit, status, and an Enabled switch. Run follows the switch straight away without a refresh. The builder, launch page, and list follow Nautobot's light and dark themes and show no version numbers.

### Fixed

- #builder-fixes - Fixed the builder so invalid saves are rejected before anything is written, every save is recorded in the change log, and a change made just before leaving is still saved. Nautobot's sidebar menus open from the builder again. The issue count follows every change, including undo, and Versions names drafts by what they are.
- #git-sync - Fixed Git sync so it never overwrites or switches off workflows built or published in Nautobot. One invalid file no longer stops the rest of the sync, and a workflow whose file is present but fails to import stays on. Removed Git workflows are switched off even when their repository link is stale, and the sync logs what it checked. Archiving no longer mistakes its own push for a newer Git change.

### Dependencies

- #lock-refresh - Refreshed the development lock file to current releases within the existing constraints, including Nautobot 3.2.7 and Django 5.2. The app still supports Nautobot >=3.0.0,<4.0.0.

### Documentation

- #user-guides - Added user guides for the builder, the launch page, inputs and CSV import, steps and Jinja, conditions, schedule and approval, Preview run, publishing and Git troubleshooting, new scripts, and permissions, with current screenshots. Also updated the install, upgrade, security, and YAML reference pages.
