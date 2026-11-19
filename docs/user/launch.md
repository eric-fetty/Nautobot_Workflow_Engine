# Running a workflow

Open **Workflows** and choose **Run** on a row. People need permission to view the workflow and to add workflow runs. See [Permissions](permissions.md).

![Launch page for Reboot a device](../assets/images/launch/01-launch-form.png)

## The launch page

- The header shows the workflow's name, category and description. People who can edit it also see **Edit**.
- The form has the workflow's [inputs](inputs.md). Required fields are marked with a red asterisk. Help text sits under each field. A field that depends on another one says so, for example *Choose Location first.*, and object pickers say how many objects are available.
- **What happens** on the right lists the steps in the order they run, with notes such as *waits for the job to finish* or *Only if “Wait for the device to come back online” is Yes*.
- **About** shows how long the job takes by hand, how many runs there were in the last 30 days and how many succeeded, the last run, and a link to **Run history**.

Problems are shown on the field as you go. The form can't be sent until required fields are filled in and every value passes its checks. The same checks run again on the server.

## When should it run?

![When should it run? with Run now, Schedule for later and In the change window](../assets/images/launch/02-when-should-it-run.png)

- **Run now** starts as soon as you submit.
- **Schedule for later** asks for a date and time.
- **In the change window** appears when the workflow is tied to ServiceNow. The run is booked for the window of the change ticket you entered and only runs if the change is approved and in an allowed state. See [Schedule and approval](schedule_approval.md).

If the workflow needs approval, the run waits as **Pending Approval** until someone approves it in **Approvals**.

For a scheduled run, the **Before** steps run once when the run is booked. If one fails, the run isn't booked.

## Tables and CSV

A table input is an editable grid with **Add row**, **Download template** and **Import CSV**. See [Tables and CSV import](inputs.md#tables-and-csv-import).

## Run history and run details

**Workflows → Run History** lists runs with their status, who started them, when, and how long they took. Filter by workflow, status and user.

![Run History](../assets/images/launch/05-run-history.png)

### Searching runs

On Run History, the search box at the top of the page shows **in: Run History**, the same way it shows **in: Devices** on the Devices list. Type there and press Enter to filter the run list. Search matches the workflow name, who started the run, its status, its change ticket, and the values entered on the run's form: text, numbers, choices (value or label), list items, table cells, and the names of the devices, VMs and other objects that were picked. For example, search for `CHG0012345` or `leaf-01` to find every run that used that ticket or switch. Remove the **in: Run History** badge to search everything instead.

Runs also show up in Nautobot's global search, from any page, under **Workflow Runs**.

Object names are recorded when the run is saved. If an object is renamed later, search for its old name, or open the object and use its **Workflow runs** panel.

### Runs on a device or virtual machine page

Device and virtual machine pages have a **Workflow runs** panel listing the latest runs whose form picked that device or VM, with the workflow, status, who started it and when. **View in Run History** (or **View all *N* runs** when there are more than five) opens Run History narrowed to that object; **Show all runs** there removes the narrowing.

Search results, the panel and the narrowed list only include runs you're allowed to see (**View runs**). The panel isn't shown to people without that permission. An administrator can show the panel on other object types, such as prefixes or locations, from the app's Settings page; see [Workflow runs panel](../admin/install.md#workflow-runs-panel-on-object-pages).

A run's page shows its status, the inputs it got, each step's result, the run's own log and the detailed log. While a run is going, the page shows progress, AWX job completion and elapsed time.

![A run's detail page](../assets/images/launch/06-run-detail.png)

| Status | Meaning |
| --- | --- |
| Scheduled | Booked for later. |
| Pending Approval | Waiting for an approver. |
| Pending | Queued for a worker. |
| Running | Running now. |
| Success | Everything required succeeded. |
| Failed | A required step failed. |
| Partial | Some steps succeeded and some failed. |
| Canceled | Canceled before it started. |

**Cancel Run** stops a run that is still **Scheduled** or **Pending Approval**. A running workflow can't be interrupted. See the [FAQ](faq.md).

**Run Again** on the run's page opens the launch form with that run's inputs already filled in. The address includes `?from_run=<id>`. The form is filled only when you are allowed to view that run; otherwise you get an empty form.
