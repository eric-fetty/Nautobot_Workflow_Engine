# Upgrading the App

Use the same controlled deployment process as for Nautobot itself. Review the
[release notes](release_notes/index.md) and [compatibility requirements](compatibility_matrix.md)
before changing the application version.

## Upgrade Guide

1. Back up the database and configuration through your approved operational process.
2. Review pending and scheduled workflow runs; arrange a maintenance window so that
    workers do not execute against a partially upgraded deployment.
3. Validate the new package and representative workflows in a non-production environment.
4. Update the web, worker, and scheduler environments consistently and complete the
    standard Nautobot app post-upgrade procedure.
5. Check synchronization, input forms, action ordering, run logs, and any integrations
    before resuming normal launches and scheduled execution.

Schema changes can make a package-only rollback unsafe. Retain the backup and a
tested restoration procedure rather than assuming an older package can use the new schema.



