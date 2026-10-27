# Permissions

Workflow Engine uses Nautobot's object permissions. A constraint, for example on category, limits which workflows or runs a person gets the permission for.

## Running workflows

| Permission | What it allows |
| --- | --- |
| `nautobot_workflow_engine.view_workflow` | See published workflows on the Workflows list and open their launch page. |
| `nautobot_workflow_engine.add_workflowrun` | Start runs. Pair it with a constrained `view_workflow` to control which workflows. |
| `nautobot_workflow_engine.view_workflowrun` | See runs in Run History, their details, and the dashboard figures built from them. |
| `nautobot_workflow_engine.change_workflowrun` | Cancel a run that is still Scheduled or Pending Approval. |

## Building workflows

| Permission | Shown as | What it allows |
| --- | --- | --- |
| `nautobot_workflow_engine.add_workflow` | Can add | Create a workflow. Its first edits are a shared draft. |
| `nautobot_workflow_engine.change_workflow` | Can change | Edit the shared draft, discard it, restore a version into the draft, and use **Preview run**. Shows **Edit** on the launch page. |
| `nautobot_workflow_engine.publish_workflow` | Publish workflows | **Publish changes**, turn **Enabled** on or off, and **Retry** a Git push. Without it the button is **Request publish**. |
| `nautobot_workflow_engine.author_code_workflow` | Author templates and scripts | Write Jinja beyond a single `{{ inputs… }}` or `{{ results… }}` reference, change a script path, and edit the YAML view. |
| `nautobot_workflow_engine.write_script_workflow` | Write scripts | Use **Create script** in [New script…](placeholder_scripts.md) and type the script's code. |

The last three are extra actions on the *Workflow* object type. To grant one, create an object permission for **nautobot_workflow_engine | workflow**, and type the action name in **Additional actions**: `publish`, `author_code` or `write_script`. Nothing else needs to be installed.

### Author templates and scripts

A label, a required flag, or help text doesn't need it. A template that is only a reference, such as `{{ inputs.device.id }}`, doesn't either. Anything beyond that, including filters and calculations, asks for the permission when it is saved. The YAML view always needs it, because a YAML document can change any code field. Someone without it uses the visual editor.

### Write scripts

Without **Write scripts**, the **New script…** panel shows the generated file read-only with **Copy code**, so the person can hand it to an admin. With it, the person can type the script's code and commit it to the repository with **Create script**. Either way the builder never runs the code. The new file is a starter that prints the values it gets; it runs like any other script once a workflow step uses it, so grant this permission only to people whose code you are happy to have in the repository.

### Preview run

**Preview run** uses the change permission. It never runs a script, calls AWX, or records a run, a scheduled job or an object change.

## Repositories are not a permission

Publishing to Git also needs a repository that is set up for it: **workflows** ticked under **Provides**, and a secrets group with an HTTP(S) token. That is a property of the repository, not a second approval. See [Getting a repository ready](publishing.md#getting-a-repository-ready).

**Enabled** is Nautobot state. It applies immediately after the first publish and doesn't make a Git commit. A YAML `enabled:` value only sets the switch the first time the file is imported.

## Examples

| Person | Grant |
| --- | --- |
| Operator who runs Networking workflows | `add_workflowrun`; `view_workflow` constrained to `{"category": "Networking"}`; `view_workflowrun` constrained to `{"workflow__category": "Networking"}`. |
| Engineer who builds workflows without code | The operator set, plus `add_workflow`, `change_workflow` and `publish`. |
| Automation engineer | The engineer set, plus `author_code` and `write_script`. |
| Manager who reads reports | `view_workflowrun` constrained to their team's category. |

More examples with constraints are in [Install and configure](../admin/install.md#permissions).
