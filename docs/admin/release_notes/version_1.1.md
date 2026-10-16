# v1.1 Release Notes

This document describes all new features and changes in the release. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Release Overview

- Optional Nautobot **3.2+** Approval Workflow integration: set `approval_required` in workflow YAML so git sync maintains a scoped Approval Workflow Definition, and immediate launches that need approval use a near-term scheduled job (`pending_approval`) instead of Celery immediate execution.
- Operators configure approver groups and stages in the Nautobot Approvals UI after sync; the app creates definition shells only.
- Includes related scheduling/UI polish, migration cleanup for an orphaned experimental column, and documentation for authors and administrators.
- Package compatibility remains Nautobot `>=3.0.0,<4.0.0` and Python `>=3.10,<3.14`. The approval feature is a no-op on Nautobot versions below 3.2.

<!-- towncrier release notes start -->


## v1.1.0 (2026-10-15)

### Added

- #approval-required - Added optional Nautobot 3.2+ approval integration via workflow YAML `approval_required`, including scoped Approval Workflow Definition sync and approval-gated immediate launches.

### Fixed

- #4 - Fixed git sync IntegrityError when an older experimental `requires_approval` database column was left behind after renaming the field to `approval_required`.

### Documentation

- #approval-required - Documented Nautobot approval-required workflows for authors, administrators, and operators, including stage configuration and troubleshooting.
