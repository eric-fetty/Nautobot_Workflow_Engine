# Installing the App in Nautobot

Here you will find detailed instructions on how to **install** and **configure** the App within your Nautobot environment.

## Prerequisites

- Nautobot 3.x with a Python version supported by both Nautobot and this app.
- Working Nautobot background workers and scheduling services.
- A supported Nautobot database and access to a trusted workflow source.
- Permission to configure apps, repository synchronization, and any optional integrations.

See [compatibility](compatibility_matrix.md) for the package version requirements.
Workflow-specific apps and Python dependencies must be available to the workers as
well as the web application. AWX and ServiceNow are optional.

## Enable the App

Use your deployment's normal Nautobot app installation process with an approved
`nautobot-workflow-engine` package. Do not assume that a public package has been
published for a source checkout. The upstream
[app installation guide](https://docs.nautobot.com/projects/core/en/stable/user-guide/administration/installation/app-install/)
describes the deployment lifecycle.

Add `nautobot_workflow_engine` to the existing `PLUGINS` list. Merge the following
settings into `PLUGINS_CONFIG`; do not replace settings for other apps.

```python
PLUGINS_CONFIG = {
    "nautobot_workflow_engine": {
        "workflow_dir": "workflows",
        "filters_dir": "filters",
        "awx_external_integration_name": "AWX",
        "awx_poll_seconds": 5,
        "show_disabled": False,
        "show_add_new_item": [],
    },
}
```

Complete your deployment's normal app post-upgrade and service restart procedures
before synchronizing workflows. Apply the same package and configuration to the web
application, workers, and scheduler.

## Configuration Reference

| Setting | Default | Purpose |
| --- | --- | --- |
| `workflow_dir` | `workflows` | Directory containing workflow YAML in the workflow source. |
| `filters_dir` | `filters` | Directory containing repository-backed Jinja filters. |
| `awx_external_integration_name` | `AWX` | Nautobot External Integration used for AWX actions. |
| `awx_poll_seconds` | `5` | Polling interval in seconds for AWX job monitoring. |
| `show_disabled` | `False` | Display disabled workflows in the workflow list. |
| `show_add_new_item` | Environment-derived | Model labels that offer the create-new-item choice; set an explicit list to override `SHOW_ADD_NEW_ITEM`. |

The create-new-item choice passes `__create__` to the workflow. Its action code must
handle that value; choosing it does not create an object automatically.

## Configure Workflow Content

Configure a Nautobot Git Repository for a trusted workflow source and select its
Workflow Engine content. YAML lives under `workflow_dir`, action scripts under
their declared paths, and reusable helpers conventionally under `scripts/lib/`.
Synchronize the source and inspect the synchronization result before launching runs.

Start with the [hostname tutorial](../user/app_getting_started.md). Configure
[AWX or ServiceNow](../user/external_interactions.md) only when a workflow requires it.
Use HTTPS and verified certificates for remote services.

## Jobs Installed by the App

Enabling the app registers several Nautobot jobs. Most are hidden because the
engine triggers them automatically; a few require your attention.

| Job | Visible | Purpose |
| --- | --- | --- |
| Execute Scheduled Workflow | Hidden | Runs a pre-created workflow run at its scheduled time. |
| Clean Orphaned Workflow Runs | Hidden | Deletes workflow runs stuck in **Scheduled** or **Pending** with no matching scheduled job. |
| Sync ServiceNow Scheduled Workflows | Visible | Revalidates scheduled ServiceNow-backed runs against their change tickets. See [ServiceNow](../user/external_interactions.md#keeping-scheduled-runs-in-sync-with-servicenow). |

### Cleaning Up Orphaned Workflow Runs

A run becomes orphaned when it is left in **Scheduled** or **Pending** but has no
scheduled job to execute it, for example after the underlying scheduled job is
removed. Orphaned runs block normal operations and clutter run history. The
**Clean Orphaned Workflow Runs** job deletes those orphaned records so history
reflects only live runs.

Run it on demand or schedule it to run periodically if you expect stuck or
orphaned runs. It deletes only runs that have no corresponding scheduled job;
runs with a live scheduled job are left untouched.

!!! warning
    This job deletes workflow run records. Review its logs after the first runs
    and schedule it at a cadence that matches your operational expectations.

## Permissions

| Permission | Level | Purpose |
| --- | --- | --- |
| `nautobot_workflow_engine.view_workflow` | Object | Browse (and launch) specific workflows. |
| `nautobot_workflow_engine.add_workflowrun` | Model | Grant the ability to launch runs at all. |
| `nautobot_workflow_engine.view_workflowrun` | Object | View specific run records and their reports. |
| `nautobot_workflow_engine.change_workflowrun` | Object | Cancel a specific run while it is scheduled. |

`view_workflow`, `view_workflowrun`, and `change_workflowrun` are **object-level**
permissions: use a Nautobot object permission with a constraint to scope them to a
subset of objects. `add_workflowrun` is a **model-level** capability: a user who
holds it for any workflow can launch runs, so pair it with a scoped
`view_workflow` to control *which* workflows they may actually run.

### Scoping by Name, Category, or Entirely

The same permission can be granted broadly or narrowly. The scope is set by the
object permission's **constraint** (a JSON filter over the model's fields).

- **Entirely** — no constraint (the permission applies to all objects).
- **By category** — `{"category": "Networking"}` on the **Workflow** model, or
  `{"workflow__category": "Networking"}` on the **WorkflowRun** model.
- **By name** — `{"name": "Echo Hostname"}` on the **Workflow** model, or
  `{"workflow__name": "Echo Hostname"}` on the **WorkflowRun** model.

Constraints use the field names of the model being authorized. A Workflow
constraint such as `{"category": "Networking"}` does **not** apply to WorkflowRun
objects; for runs, reference the related workflow with `workflow__category` or
`workflow__name`.

### Example: A Team That Runs Only One Category

Grant the **Networking** team access to launch workflows in the
`Networking` category and nothing else:

1. Create a Nautobot group, e.g. `networking-operators`.
2. Grant the group `add_workflowrun` (model permission, no constraint).
3. Create an object permission for `view_workflow` with the constraint
   `{"category": "Networking"}` and assign it to the group.

Members of the group now see only `Networking` workflows and can launch only those.
The model-level `add_workflowrun` gives them the ability to launch; the constrained
`view_workflow` limits the scope to their category.

### Example: A Manager Who Reads Only Their Team's Reports

Grant a manager read access to the run records (and the reporting dashboard) for the
workflows their team can run, without giving them access to other teams' data:

1. Create (or reuse) a Nautobot group, e.g. `networking-operators`.
2. Create an object permission for `view_workflowrun` with the constraint
   `{"workflow__category": "Networking"}` and assign it to the manager (or the
   group).

The reporting dashboard is built from the runs the viewer is allowed to see. The
view is filtered to the WorkflowRun objects the user has `view_workflowrun`
permission for, so a manager constrained to `workflow__category = Networking` sees
only that category's runs in run history, status summaries, success rates, and
time-savings figures. Runs outside the constraint are excluded from every report.

To also let the manager *run* the workflows (not just view reports), add
`add_workflowrun` (model) and a constrained `view_workflow`
(`{"category": "Networking"}`) as in the previous example.

### Verifying and Revising

Assign object permissions through Nautobot and verify visibility using
representative accounts before widening access. When a team's scope changes,
update the constraint on the existing object permission rather than creating a
new one, and re-check both the workflow list and the reporting dashboard.
See [security and data handling](security.md) before granting access to
operational workflows.
