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
schedule time} · run {short run ID}` (for example `Privileged Change · eric · CHG0001234 · run 1a2b3c4d`).
The short run ID keeps two requests for the same change ticket apart. For ServiceNow
schedules the ticket comes from the input named by `schedule.servicenow.change_input_key`;
otherwise the scheduled UTC time is shown.

## Why aren't tags in my YAML?

Workflow tags are Nautobot tags, the same as on devices and locations. They're stored in
Nautobot, not in the workflow definition, so they never appear in the YAML, in a version,
or in Git. A Git sync updates the definition and leaves the tags alone, and a change to a
file in the repository can't add or remove them. Set them in the builder's **Details**
step, through the builder API, or for many workflows at once with **Bulk edit tags** on the
Workflows list. See [Tags](builder.md#tags).

## Why doesn't a tag show up in the builder's Tags picker?

The tag doesn't allow workflows yet. Open **Organization → Tags**, edit the tag, and add
**Workflow Engine | workflow** to **Content Type(s)**.

## Why was my run "Blocked by freeze"?

It came due during a [change freeze](../admin/change_freezes.md) that covers the workflow,
so nothing in it ran. The run page names the freeze. It isn't retried: run it again after the
freeze, or ask someone with **Override change freezes** to run it with a reason. A run booked
with an override is also blocked if the person who booked it lost that permission before it
fired.

## Why can't I remove a tag or change a category?

The change would take the workflow out of an active or upcoming change freeze, which is the
same as overriding it, so it needs **Override change freezes**. The message names the freeze.
Bulk edit skips those workflows and says so, and a Git sync leaves them unchanged and logs
`E-NWE-302`.
