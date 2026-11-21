# Examples Tutorial

This tutorial walks the thirteen example workflows in [Nautobot-Demo-Workflows](https://github.com/eric-fetty/Nautobot-Demo-Workflows), in order, from a first launch to a maintenance-window change, and then three examples that branch on how earlier steps turned out. Each example adds one or two features. The YAML, scripts, and a short "what you'll learn / how to run / next" note for every workflow live in that repository.

The first five examples never change Nautobot inventory. Examples 6–13 have an **Apply these changes in Nautobot** switch that defaults to off. Leave it off to only plan. Turn it on only in a lab. None of these examples contact or reboot a live device, and none of them require AWX.

## Before you start

1. Install and configure the app. See [Install and configure](../admin/install.md). Background workers must be running.
2. Add [Nautobot-Demo-Workflows](https://github.com/eric-fetty/Nautobot-Demo-Workflows) as a Nautobot Git Repository. Under **Provides**, enable the Workflow Engine content. Synchronize it.

    ![Git Repository with Workflow Engine content selected](../assets/images/getting-started/01-git-repo-workflow-content.png)

3. Confirm the thirteen **Examples** workflows appear on the **Workflows** list.

    ![The Workflows list with the ten Examples workflows](../assets/images/examples/workflows-list.png)

4. Use an account with permission to view workflows, run them, and view run results. See [Permissions](permissions.md).
5. For examples 3 and 6–13, seed the lab inventory (locations AMS01 and NYC02, demo switches, and a fake **ServiceNow (demo)** connection). Do not run the seed in production. The [lab setup section](https://github.com/eric-fetty/Nautobot-Demo-Workflows#lab-setup) in the examples repository describes the Job and the nbshell command.

Examples 1, 2, 4, and 5 can be run before you seed.

Each example below shows its launch form as it appears after the lab seed. The screenshots were taken on Nautobot 3.2 with the examples repository synchronized.

## 1. Echo a hostname

Workflow: [`01-echo-hostname`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/01-echo-hostname.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/01-echo-hostname.md).

![Launch form for 01. Echo a hostname](../assets/images/examples/launch-01-echo-hostname.png)

This is the first run: one text input and one Nautobot script that returns the hostname you enter.

1. Choose **Run** on **01. Echo a hostname**.
2. Enter `router.example.com`.
3. Leave **When should it run?** on **Run now** and submit.
4. Open the run and inspect its status, the returned hostname, and the execution log.

    ![Successful 01. Echo a hostname run with its inputs, user log, and debug log](../assets/images/examples/run-01-detail.png)

The workflow's **Manual effort per run (minutes)** is what the [dashboard](app_reporting.md) uses to estimate time saved after a successful run.

You can also build this same workflow in the builder without writing YAML. See [Or build it in the builder](app_getting_started.md#or-build-it-in-the-builder).

## 2. Reserve a VLAN ID

Workflow: [`02-reserve-vlan-id`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/02-reserve-vlan-id.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/02-reserve-vlan-id.md).

![Launch form for 02. Reserve a VLAN ID](../assets/images/examples/launch-02-reserve-vlan-id.png)

This example adds a whole-number input with a minimum and a maximum, help text, a placeholder, and [validation rules](inputs.md#validation-rules). The VLAN name has two rules: the builder's **VLAN name** preset, then a stricter site convention that allows only lowercase letters, numbers, and dashes. The form shows the message of the first rule a value fails, so `users floor 2` gets the preset's message and `Users_Floor_2` gets the convention's. The optional **Change ticket** uses the **ServiceNow change number** preset with the `IGNORECASE` flag. An empty value is not checked.

Try an ID outside 1–4094, one of those names, or the ticket `CHG12`, and watch the form reject them before you submit. The server checks the same rules again when the form is sent.

![The VLAN ID and name rejected on the launch form](../assets/images/examples/launch-02-validation-errors.png)

The script only returns a plan. Nothing is created in Nautobot.

## 3. Plan an interface description

Workflow: [`03-plan-interface-description`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/03-plan-interface-description.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/03-plan-interface-description.md).

![Launch form for 03. Plan an interface description](../assets/images/examples/launch-03-plan-interface-description.png)

Run the lab seed first. This example introduces Nautobot object pickers: a location, then a device at that location, then an interface on that device, using `depends_on` and a filter. A choice of description style shows or hides later fields with `show_if`. A step condition decides whether the plan step runs.

![Inputs step with an object field and its filter open, and the Launch form preview](../assets/images/builder/03-inputs.png)

Pick AMS01, `ams01-acc-sw01`, and `Ethernet1/1`. Leave the style on the built-in option for the first run, then run again with a custom description and confirm that field appears. This workflow still only plans; it does not change the interface.

This is a good time to try [Preview run](preview_run.md) in the builder. It shows which steps would run, with the values from the launch-form preview, and it never executes a script or calls AWX.

![Preview run for 07. Turn up a port: one step would run and two are skipped](../assets/images/examples/builder-preview-run.png)

## 4. Normalize a hostname

Workflow: [`04-normalize-hostname`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/04-normalize-hostname.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/04-normalize-hostname.md).

![Launch form for 04. Normalize a hostname](../assets/images/examples/launch-04-normalize-hostname.png)

A custom Jinja filter, `normalize_for_dns`, lives in `filters/string_filters.py` and imports a shared helper from `scripts/lib`. Enter `My Router Host` or `bad_name_host` and confirm the action receives `my-router-host` or `bad-name-host`. The filter is used both in a parameter and in a `when` condition. Nothing in inventory is changed. See [Custom filters](../dev/extending.md).

## 5. Record a planned change

Workflow: [`05-record-planned-change`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/05-record-planned-change.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/05-record-planned-change.md).

![Launch form for 05. Record a planned change](../assets/images/examples/launch-05-record-planned-change.png)

The change-ticket field comes from a shared fragment, included with `{% include "templates/inputs/standard_change_inputs.yaml" %}`. Include paths are resolved relative to the workflow file, and the fragment must use the indentation expected at the insertion point. The ticket must look like `CHG0031245`. Later examples reuse the same fragment. Nothing in inventory is changed.

## 6. Add VLANs

Workflow: [`06-add-vlans`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/06-add-vlans.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/06-add-vlans.md).

![Launch form for 06. Add VLANs](../assets/images/examples/launch-06-add-vlans.png)

This example introduces a table input (`listdict`) with typed columns, unique VLAN IDs, and row limits, plus a list of extra trunk ports, a pick-many tags field, and a several-objects picker for switches. On the launch form the table is an editable grid.

Validation rules work per cell and per line. Every **Name** cell must pass the **VLAN name** preset and the lowercase convention, and the error names the row, for example *Row 1: Name: Use lowercase letters, numbers, and dashes.* Every line of **Extra trunk ports** must pass the **Interface name** preset and an Ethernet-or-Port-Channel rule, so `Vlan10` is rejected with *Line 2: Trunk ports must be Ethernet or Port-Channel interfaces.*

![The VLANs table with Add row, Download template and Import CSV](../assets/images/examples/launch-06-table-input.png)

**Download template** and **Import CSV** read a file in your browser. The file is not uploaded or stored. Only the rows in the grid are sent with the form.

![A CSV import with the rows that need fixing marked](../assets/images/examples/launch-06-csv-imported.png)

**Apply these changes in Nautobot** defaults to off. Leave it off to only plan. Turn it on only in this lab, to create (or reuse) VLANs at the location. Do not use Apply in production.

## 7. Turn up a port

Workflow: [`07-turn-up-a-port`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/07-turn-up-a-port.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/07-turn-up-a-port.md).

![Launch form for 07. Turn up a port](../assets/images/examples/launch-07-turn-up-a-port.png)

Steps are grouped into **Before**, **Main**, and **Always run at the end**. If a Before step fails, nothing else runs. Always-after steps still run after a main failure.

![Steps timeline with Before, Main and Always run at the end](../assets/images/builder/04-steps.png)

The Before step simulates a reachability check: it confirms the device exists in Nautobot and reports reachable. No packets are sent. The main step runs only if that check succeeded:

![Condition: run only if an earlier step's output field is true](../assets/images/builder/08-condition-earlier-step-output.png)

With Apply off, the main step only plans:

![07. Turn up a port with Apply off: the interface was not changed](../assets/images/examples/run-07-plan-only.png)

With Apply on, it sets the interface description in this lab, and the always-after step adds a journal entry only when that main step succeeded.

[Preview run](preview_run.md) follows these conditions with the values in the launch-form preview. When a condition depends on an earlier step's output, it says that the outcome can only be known in a real run.

## 8. Approve a maintenance reboot

Workflow: [`08-approve-maintenance-reboot`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/08-approve-maintenance-reboot.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/08-approve-maintenance-reboot.md).

![Launch form for 08. Approve a maintenance reboot](../assets/images/examples/launch-08-approve-maintenance-reboot.png)

This workflow sets `approval_required: true`. Approving a run is configured in Nautobot, not as a Workflow Engine permission.

![Schedule & approval step with approval and the ServiceNow change window](../assets/images/builder/09-schedule-approval.png)

After Git sync, open **Approvals → Workflow Definitions**, find **Workflow Engine: 08-approve-maintenance-reboot**, and add at least one stage (an approver group and the minimum number of approvers). Until a stage exists, launches stay **Pending Approval**. Approvers use **Approvals → Approval Dashboard**. This example only records a plan. It never reboots a device.

![A run waiting for approval](../assets/images/examples/run-08-pending-approval.png)

![Approval Dashboard with runs waiting for approval](../assets/images/examples/approvals-dashboard.png)

![Approving a stage with a comment](../assets/images/examples/approvals-approve-stage.png)

After the stage is approved, the run starts on its own.

On Nautobot versions before 3.2 the setting is kept and has no effect. See [Schedule and approval](schedule_approval.md).

## 9. Schedule inside a change window

Workflow: [`09-schedule-change-window`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/09-schedule-change-window.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/09-schedule-change-window.md).

![Launch form for 09. Schedule inside a change window](../assets/images/examples/launch-09-schedule-change-window.png)

The launch form offers **Run now**, **Schedule for later**, and, when the workflow declares a ServiceNow window, **In the change window**.

![When should it run? with Run now, Schedule for later and In the change window](../assets/images/examples/launch-09-when-should-it-run.png)

The lab seed creates a fake External Integration named **ServiceNow (demo)** so this setting can be saved. It does not talk to a real ServiceNow. In a lab, choose **Schedule for later**, pick a time a few minutes ahead, and submit. **In the change window** succeeds only when you have pointed an integration at a real instance and enabled the sync job; see [Integrations](external_interactions.md#servicenow-change-windows).

Open **Workflows → Run History** to find the booked run.

![Run History list of workflow runs](../assets/images/examples/run-history.png)

While a run is still **Scheduled** or **Pending Approval**, **Cancel Run** is available on the run's page. A running workflow cannot be interrupted.

![Scheduled run with Cancel Run available](../assets/images/examples/run-09-scheduled.png)

After a run finishes, **Run Again** on that page opens the launch form with the earlier inputs already filled in. The address includes `?from_run=<id>`. The form is filled only when you are allowed to view that run.

![Run Again opens the launch form with the earlier inputs](../assets/images/examples/launch-01-run-again.png)

## 10. Maintenance-window change

Workflow: [`10-maintenance-window-change`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/10-maintenance-window-change.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/10-maintenance-window-change.md).

![Launch form for 10. Maintenance-window change](../assets/images/examples/launch-10-maintenance-window-change.png)

This capstone combines the earlier rungs: a VLAN table, several devices, the shared change ticket, a Before reachability check, a main plan or apply, an always-after journal entry when Apply succeeded, and a backout note when the main step failed. It also opts into Nautobot approval and declares the same ServiceNow change window as example 9.

Add an approval stage for **Workflow Engine: 10-maintenance-window-change**, leave Apply off for the first run, and use **Schedule for later** or **Run now**. **In the change window** needs a real ServiceNow, as in example 9.

With Apply on and the stage approved, the run creates the VLANs and adds a journal note. The backout step is skipped because the main step succeeded.

![10. Maintenance-window change applied in the lab](../assets/images/examples/run-10-applied.png)

## Branching on earlier steps

Examples 11–13 show what to do after a step has run: recover when it failed, close out when it succeeded, or choose a path from a value it returned. Every step's result is available to later steps as `results.<step name>`, with:

- `status`: `success`, `failed`, or `skipped` (its `when` was false)
- `return_value`: whatever the script returned, for example a dict
- `error`: the error text, when the step failed

Three rules make these patterns work:

1. **Main steps keep going after a failure.** A later Main step can test `results.verify.status == 'failed'` and run a recovery. Before steps are different: the first failure stops the run.
2. **Guard with `is defined`.** Write `results.verify is defined and results.verify.status == 'failed'`, so the condition is simply false when that step never produced a result.
3. **A recovered run still ends Failed.** The run's status is **Failed** whenever any Main step failed, even when the recovery that followed worked. The run history then says truthfully that the change did not land, and the debug log shows the recovery succeeded. If an outcome is not really a failure, return it as a value instead and branch on that, as example 13 does.

The run page shows each step's outcome in the **Debug Log**: `Starting action`, `Action '…' failed: …`, and `Skipped action '…' (when=… → False)`. The **User Log** shows what each script printed.

Like examples 6–10, these have **Apply these changes in Nautobot** off by default, and each has a switch that forces the failure path, so you can see both branches in a lab with no devices. The lab seed adds what they need: VLANs 110 and 120 at each site, a deliberately full management prefix and a fallback prefix per site, and an uplink cable on each site's first access switch.

## 11. Turn up a port with verify and rollback

Workflow: [`11-turn-up-with-rollback`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/11-turn-up-with-rollback.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/11-turn-up-with-rollback.md).

The everyday change pattern: save the port's state, change it, check it, then either put it back or close it out.

| Step | Runs when | Does |
| --- | --- | --- |
| `snapshot` | always | Returns the port's description, enabled, mode, VLANs, and status |
| `apply_change` | `snapshot` succeeded | Sets the port as an access port in **Planned** status |
| `verify` | `apply_change` succeeded | Reads the port back and compares it with the plan. Fails on a mismatch, or when **Simulate a failed verification** is on |
| `rollback` | `verify` or `apply_change` failed | Restores the port from `results.snapshot.return_value` and adds a note |
| `close_out` | `verify` succeeded | Sets the port **Active** and adds a note |

![Launch form for 11. Turn up a port with verify and rollback](../assets/images/examples/launch-11-turn-up-with-rollback.png)

The recovery step and the success step use opposite conditions on the same earlier step, and the rollback receives the first step's whole return value:

```yaml
  - type: nautobot
    name: rollback
    script: scripts/rollback_interface.py
    when: "{{ results.snapshot is defined and results.snapshot.status == 'success' and ((results.verify is defined and results.verify.status == 'failed') or (results.apply_change is defined and results.apply_change.status == 'failed')) }}"
    with:
      interface: "{{ inputs.interface.id }}"
      snapshot: "{{ results.snapshot.return_value }}"
      reason: "{{ results.verify.error if (results.verify is defined and results.verify.status == 'failed') else results.apply_change.error }}"
      apply: "{{ inputs.apply }}"
  - type: nautobot
    name: close_out
    script: scripts/close_out_interface.py
    when: "{{ results.verify is defined and results.verify.status == 'success' }}"
```

Step values reach a script as text, so the rollback script turns `snapshot` back into a dict with `as_dict` from the examples repository's `scripts/lib/params.py`.

With **Simulate a failed verification** and **Apply** on, the port is changed, verification fails, and the rollback puts every setting back. The run ends **Failed** because `verify` failed:

![11 with a failed verification: the run is Failed and the User Log shows the rollback](../assets/images/examples/run-11-rollback.png)

The Debug Log shows `verify` failing, `rollback` starting, and `close_out` skipped by its condition:

![Debug Log: verify failed, rollback ran, close_out skipped](../assets/images/examples/run-11-rollback-debug-log.png)

With the simulation off, `verify` passes, `rollback` is skipped, and `close_out` marks the port Active. The run ends **Success**:

![User Log for the success path: verified and closed out](../assets/images/examples/run-11-closed-out-user-log.png)

## 12. Next free management IP with a fallback prefix

Workflow: [`12-next-free-ip-with-fallback`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/12-next-free-ip-with-fallback.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/12-next-free-ip-with-fallback.md).

A fallback rather than a rollback: when the preferred management pool is full, take an address from a secondary pool instead.

| Step | Runs when | Does |
| --- | --- | --- |
| `reserve_primary` | always | Takes the next free address in the preferred prefix. Fails if the prefix is full |
| `reserve_fallback` | `reserve_primary` failed | Takes the next free address in the fallback prefix |
| `assign_ip` | either reservation succeeded | Puts the address on the interface and makes it the device's primary IPv4 |

The lab seed creates `10.20.255.0/29` with every address used, so choosing it as the preferred prefix fails for real. Use `10.20.254.0/24` as the fallback. **Treat the preferred prefix as full** forces the same path with any prefix.

![Launch form for 12 with the full prefix preferred](../assets/images/examples/launch-12-next-free-ip-with-fallback.png)

The last step runs when either reservation worked, and picks the address from whichever one did:

```yaml
  - type: nautobot
    name: assign_ip
    script: scripts/assign_primary_ip.py
    when: "{{ (results.reserve_primary is defined and results.reserve_primary.status == 'success') or (results.reserve_fallback is defined and results.reserve_fallback.status == 'success') }}"
    with:
      address: "{{ results.reserve_primary.return_value.address if results.reserve_primary.status == 'success' else results.reserve_fallback.return_value.address }}"
```

On the fallback path the device gets its address from the fallback prefix, and the run still ends **Failed** because the preferred reservation failed:

![12 on the fallback path: Failed, with the fallback address assigned](../assets/images/examples/run-12-fallback.png)

![Debug Log: reserve_primary failed, then reserve_fallback and assign_ip ran](../assets/images/examples/run-12-fallback-debug-log.png)

With a preferred prefix that has room, `reserve_fallback` is skipped and the run ends **Success**.

## 13. Software upgrade with pre and post checks

Workflow: [`13-upgrade-with-pre-post-checks`](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/13-upgrade-with-pre-post-checks.yaml). Notes: [what you'll learn](https://github.com/eric-fetty/Nautobot-Demo-Workflows/blob/main/workflows/13-upgrade-with-pre-post-checks.md).

Baseline the switch, upgrade it, and compare. Here the outcome is a **value**, not a failure: the post-check succeeds and returns how much drifted, and the last two steps branch on that number.

| Step | Phase | Runs when | Does |
| --- | --- | --- | --- |
| `precheck` | Before | always | Returns enabled interfaces, cabled neighbors, and VLANs |
| `upgrade` | Main | always | Simulated upgrade. The device is never contacted |
| `postcheck` | Main | `upgrade` succeeded | Compares the switch with `results.precheck.return_value`. Returns `drift` and `differences` |
| `close_success` | Main | `results.postcheck.return_value.drift == 0` | Notes that the upgrade was verified |
| `open_backout` | Main | `results.postcheck.return_value.drift > 0` | Notes that backout is needed, with one line per difference |

![Launch form for 13. Software upgrade with pre and post checks](../assets/images/examples/launch-13-upgrade-with-pre-post-checks.png)

```yaml
  - type: nautobot
    name: open_backout
    script: scripts/record_change_note.py
    when: "{{ results.postcheck is defined and results.postcheck.status == 'success' and results.postcheck.return_value.drift > 0 }}"
    with:
      message: "Upgrade to {{ inputs.target_version }} needs backout: the post-check found {{ results.postcheck.return_value.drift }} difference(s) from the baseline."
      details: "{{ results.postcheck.return_value.differences }}"
```

With **Simulate drift after the upgrade** on, the post-check reports the uplink down and its neighbor lost, `open_backout` runs, and `close_success` is skipped. Because no step failed, the run ends **Success**:

![13 with drift: Success, and the User Log lists the differences in the backout note](../assets/images/examples/run-13-drift.png)

Without drift, `close_success` runs and `open_backout` is skipped:

![Debug Log for a clean upgrade: open_backout skipped](../assets/images/examples/run-13-clean-debug-log.png)

Because `precheck` is a Before step, a failed baseline stops the run before the upgrade starts.

## Git sync and publish

The published workflow lives in Nautobot and in a file in its Git repository. After you synchronize the examples repository, the thirteen workflows are the live versions people can run. If you edit one in the builder and have the *Publish workflows* permission, **Review & publish** writes a new version back to Git.

![Review & publish with checks, what changes, and the Publish changes list](../assets/images/builder/12-review-publish.png)

The header shows whether Git has the published version. If a push fails, **Not synced · Retry** explains why. When someone publishes a change straight to Git while you edit, a banner offers **Compare** or **Load their version**. See [Publishing and versions](publishing.md) and [Two-way Git sync](git_sync.md).

A repository has to be set up for publishing once: **Provides** includes **workflows**, and a secrets group with an HTTP(S) token is in place.

![Git repository Provides with workflows ticked](../assets/images/publishing/03-git-repository-provides.png)

## Preview run

**Preview run** in the builder renders conditions and parameters from the launch-form preview. It never runs a script, calls AWX, or records a run. You need the *Build workflows* permission to use it. See [Preview run](preview_run.md).

![Launch form preview in the builder](../assets/images/examples/builder-launch-form-preview.png)

## Run history, cancel, and Run Again

**Workflows → Run History** lists runs with their status, who started them, when, and how long they took.

![Run History](../assets/images/examples/run-history.png)

A run's page shows the inputs, each step's result, and the logs. **Cancel Run** works only while the run is **Scheduled** or **Pending Approval**. **Run Again** prefills the launch form from that run when you are allowed to view it, using `?from_run=<id>`. See [Running a workflow](launch.md).

## Time-savings dashboard

After you have a few successful runs, open the reporting dashboard. It uses each workflow's **Manual effort per run (minutes)** minus the automated run time.

![Dashboard overview](../assets/images/reporting/01-dashboard-overview.png)

![Time saved](../assets/images/reporting/03-dashboard-time-savings.png)

See [Dashboard and time saved](app_reporting.md).

## Permissions

The app creates ready-made permissions when it is installed. They are not given to anyone until an admin assigns them.

![The ready-made Workflow Engine permissions](../assets/images/admin/04-default-permissions.png)

A typical operator needs **Run workflows** and **View runs**. Approving a run is not a Workflow Engine permission; it is set on the Nautobot approval definition. See [Permissions](permissions.md).

## New scripts from the builder

In a step, **New script…** writes a starter script that prints the values it receives. The builder never runs that code to find out what the script needs, and it never executes the new file when you create it. *Write scripts* is required to type the code and commit it; without that permission the panel is read-only with **Copy code**.

![New script panel](../assets/images/builder/14-new-script-editable.png)

See [New scripts from the builder](placeholder_scripts.md).

## Next

- [The workflow builder](builder.md) and the [YAML reference](app_use_cases.md) for authoring your own workflows.
- [Integrations](external_interactions.md) if you later add optional AWX job templates or a real ServiceNow connection.
- [Security and data handling](../admin/security.md) before you submit operational data.
