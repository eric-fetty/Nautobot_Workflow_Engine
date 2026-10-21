# Permissions

The builder uses three workflow permissions. Object permissions still apply, so a constraint on category or another field limits which workflows a person can open.

| Permission | What it allows |
| --- | --- |
| `nautobot_workflow_engine.view_workflow` | See published workflows on the Workflows list and open Run. |
| `nautobot_workflow_engine.add_workflow` | Create a workflow. The first edits are a shared draft. |
| `nautobot_workflow_engine.change_workflow` | Edit the shared draft, discard it, restore a version into the draft, and run **Test run**. |
| `nautobot_workflow_engine.publish_workflow` | **Publish changes**, turn **Enabled** on or off, and retry a Git push. Without it the button is **Request publish**. |
| `nautobot_workflow_engine.author_code_workflow` | Change Jinja that is not a single `{{ inputs... }}` or `{{ results... }}` reference, change a script path, and use **Edit YAML**. |

**Author templates and scripts** is the permission shown in the UI as *Author templates and scripts*. A label, a required flag, or help text does not need it. A template such as `{{ inputs.device }}` is in the safe subset and does not need it either. Anything beyond that subset, including filters and nested expressions, asks for the permission when you save.

**Edit YAML** always asks for that permission, because a YAML document can change any code field. Someone without it still uses the form.

**Test run** uses the change permission. It renders templates in the sandbox and does not write a run, a scheduled job, or an object change.

Every workflow opens in the same editor. There is no read-only Git workflow and no separate Launch or Manage menu. **Enabled** is Nautobot state: it applies immediately after the first publish and does not make a Git commit. A YAML `enabled:` value only sets that switch the first time the file is imported.

Publishing to Git also needs a repository that provides both workflow contents and has an HTTP token. That is a property of the repository, not a second approval. See [Two-way Git sync](git_sync.md).
