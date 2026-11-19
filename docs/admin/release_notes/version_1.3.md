# v1.3 Release Notes

This document describes all new features and changes in the release. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release Overview

Version 1.3 is a security and fixes release. It is a minor release rather than a patch because it drops Python 3.10.

- **Security.** Run Again only pre-fills the launch form from runs the person may view. The locked dependencies move to Nautobot 3.2.9, which brings Django 5.2.19, Pillow 13 and social-auth-core 6.2.
- **Python 3.11 or later.** Pillow 13 no longer supports Python 3.10, so the package now requires Python 3.11 through 3.13. On Python 3.10, stay on 1.2.x or upgrade Python first.
- **Fixes found while testing the examples.** Launch-form tables no longer redraw themselves non-stop. The same workflow can be scheduled twice for the same change ticket. Preview run accepts inputs that pick several objects.
- **Examples.** The examples now live in the [Nautobot-Demo-Workflows](https://github.com/eric-fetty/Nautobot-Demo-Workflows) repository, with a ten-step tutorial and screenshots. See [Examples tutorial](../../user/examples_tutorial.md).

### Upgrading

- There are no new migrations. Run `nautobot-server post_upgrade` as usual.
- Compatibility: Nautobot `>=3.0.0,<4.0.0` and Python `>=3.11,<3.14`.
- Scheduled jobs created from now on have names that end with a short run ID, for example `Privileged Change · eric · CHG0001234 · run 1a2b3c4d`. Existing scheduled jobs keep their names and still run.

<!-- towncrier release notes start -->


## v1.3.0 (2026-11-19)

### Security

- #run-again-permission - The launch form's Run Again pre-fill now only uses a previous run the person is allowed to view, and ignores a missing or invalid run id without error.
- #nautobot-3.2.9-dependencies - Updated the development lock file to Nautobot 3.2.9 (Django 5.2.19, Django REST framework 3.18.3, Pillow 13.0.0, social-auth-core 6.2.0, OpenTelemetry 1.45.2, oauthlib 4.0.1) to pick up multiple upstream security fixes, and updated the docs theme `mkdocs-material` to 9.7.7 to fix a cross-site scripting issue in the bundled documentation's search suggestions.

### Added

- #examples-tutorial - Added an examples tutorial that walks the ten workflows in the Nautobot-Demo-Workflows repository, and pointed Getting Started and the README at that repository.

### Removed

- #examples-folder - Removed the in-tree examples/ folder. The canonical examples now live in the Nautobot-Demo-Workflows repository.
- #python-3.10 - Dropped support for Python 3.10. The package now requires Python 3.11 through 3.13, which lets every supported Python version get Nautobot 3.2.9 and its security fixes. If you run Python 3.10, stay on Workflow Engine 1.2.x or upgrade Python first.

### Fixed

- #preview-multiobject - Preview run now accepts inputs that pick several objects, such as a Devices field, instead of reporting the selection as an invalid ID. Pick-many choices and lists also keep their values in a preview.
- #scheduled-job-names - Scheduling the same workflow twice with the same change ticket now works. Each scheduled run's name now ends with a short run ID, for example "Change VLANs · eric · CHG0001234 · run 1a2b3c4d".
- #table-redraw-loop - The launch form table (for example a VLAN list) no longer keeps redrawing itself after it refreshes while a cell is selected, so drop-downs in the table, such as Role, work again.

### Documentation

- #examples-screenshots - Added screenshots of every example's launch form, runs, approvals, scheduling, and Preview run to the examples tutorial, and fixed tutorial images that rendered as code blocks.
