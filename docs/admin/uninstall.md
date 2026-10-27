# Uninstall the App from Nautobot

Removing the app can make workflow definitions and historical runs unavailable.
Decide whether to retain or permanently delete that data before proceeding.

## Database Cleanup

Back up the database, workflow sources, and required run records using your approved
operational process. Cancel scheduled runs and allow active work to complete before
disabling workers or the app.

Reversing app migrations deletes app data and may affect related objects. It is not a
routine prerequisite for disabling the app. Have the deployment administrator review
dependencies and retention requirements before any destructive database cleanup.

## Remove App configuration

Remove the app entry from `PLUGINS` and any of its settings from `PLUGINS_CONFIG` in all web,
worker, and scheduler environments. Values saved on the app's Settings page are stored in
Nautobot's Constance configuration and are no longer used once the app is disabled.
Remove workflow content-provider selections from configured sources as needed. Keep
shared integrations and Secrets Groups unless no other application or workflow uses
them.

## Uninstall the package

Remove `nautobot-workflow-engine` from `local_requirements.txt` (or your deployment's
equivalent dependency configuration), uninstall the package
(`pip uninstall nautobot-workflow-engine`), and follow the normal rebuild/restart
process. Verify Nautobot starts without the app and that no scheduled task or source
synchronization still depends on it.
