# Frequently Asked Questions

## Can I cancel a running workflow?

No. Running workflows cannot be interrupted.

## What can be canceled?

Runs in **Scheduled** or **Pending Approval** status can be canceled from the run detail page.

## What permission is required to cancel?

**Run workflows** for that workflow (its `cancel` action), plus **View runs** to open the
run's page. It covers any Scheduled or Pending Approval run of the workflow, not only
your own. An older `change_workflowrun` permission still works.

## Why is my approval-required workflow stuck in Pending Approval?

Usually the synced Approval Workflow Definition has no stages yet. After enabling
`approval_required: true` and syncing the Git repository, open
**Approvals → Workflow Definitions**, find `Workflow Engine: <workflow.key>`, and add at
least one stage with an approver group. Then approve the request under
**Approvals → Approval Dashboard**.

This feature requires Nautobot 3.2 or later. On older versions the flag is ignored.

## Where do I approve a workflow run?

Use Nautobot's **Approvals → Approval Dashboard**. The Workflow Engine submits a
scheduled job for approval; it does not provide a separate in-app approval UI.

Pending items appear with a title like `{Workflow name} · {requester} · {change ticket or
schedule time}` (for example `Privileged Change · eric · CHG0001234`). For ServiceNow
schedules the ticket comes from the input named by `schedule.servicenow.change_input_key`;
otherwise the scheduled UTC time is shown.
