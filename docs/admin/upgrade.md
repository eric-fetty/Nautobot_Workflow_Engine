# Upgrading the App

Use the same controlled deployment process as for Nautobot itself. Review the
[release notes](release_notes/index.md) and [compatibility requirements](compatibility_matrix.md)
before changing the application version.

## Upgrade Guide

1. Back up the database and configuration through your approved operational process.
2. Review pending and scheduled workflow runs; arrange a maintenance window so that
   workers do not execute against a partially upgraded deployment.
3. Validate the new package and representative workflows in a non-production environment.
4. Upgrade the package (for example `pip install --upgrade nautobot-workflow-engine`),
   update the web, worker, and scheduler environments consistently, and complete the
   standard Nautobot app post-upgrade procedure (`nautobot-server post_upgrade` and
   service restarts).
5. Check synchronization, input forms, action ordering, run logs, and any integrations
    before resuming normal launches and scheduled execution.
6. If you use `approval_required` workflows, confirm you are on Nautobot **3.2+**, run
    migrations, re-sync the workflow Git repository, and configure at least one stage
    on each synced definition under **Approvals → Workflow Definitions** (name pattern
    `Workflow Engine: <workflow.key>`).
7. App settings are managed on the [Settings page](install.md#settings-page): the gear
   icon next to **Workflow Engine** under **Apps → Installed Apps**. Keys still set under
   `PLUGINS_CONFIG` keep working and show there as locked; remove a key from
   `nautobot_config.py` to change it on the page instead.

Schema changes can make a package-only rollback unsafe. Retain the backup and a
tested restoration procedure rather than assuming an older package can use the new schema.



