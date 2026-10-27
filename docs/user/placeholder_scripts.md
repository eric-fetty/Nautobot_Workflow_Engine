# New scripts from the builder

A workflow step of type **Nautobot script** runs a Python file from the workflow's repository. When the script you need doesn't exist yet, the builder can write a starting point for it, so the workflow can be designed now and the code written later. A new script is a friendly starter: it prints every value it receives, so you can run the workflow and see exactly what reaches the script.

## Create a script

1. Open a step, set **What it runs** to **Nautobot script**, and pick **New script…** at the end of the **Script** list.
2. Give it a **Script name**. The builder shows where it will be saved, for example *scripts/post_change_note.py in netops-workflows*.
3. The parameters come from the step: each parameter you have wired is listed under **It will ask for**, with its type taken from the input it is wired to.
4. Optionally, write **What it does**: the body of the script. It is checked as you type. Leave it empty to get the starter that prints each value.
5. Check **The file it saves**, then choose **Create script**.

![New script panel with the code box and the generated file](../assets/images/builder/14-new-script-editable.png)

The file is committed to the workflow's repository, in its scripts folder, as you. It appears in the **Script** list straight away. An existing file is never overwritten: pick another name if the builder says it already exists.

## What gets checked

The code is only read, never run. The builder checks that it is valid Python and that the file has nothing at the top level except its description, `PARAMETERS` and `def run(params)`, so nothing in it runs when the file is loaded. A problem is shown with the line of your own code it is on.

![A problem in the script code, shown with its line number](../assets/images/builder/16-new-script-checked.png)

The builder never imports, evaluates or runs a script, not when creating it, not in the preview, and not in [Preview run](preview_run.md).

## The file

```python
"""Post change note

Created in the workflow builder. Edit it in the repository.
"""

PARAMETERS = {
    "device": {"type": "object", "required": True, "model": "dcim.Device"},
    "message": {"type": "string", "required": True},
}


def run(params):
    """Do the work for the workflow step. Return a dict for later steps to read."""
    # Replace this with your logic.
    print(f"device: {params.get('device')!r}")
    print(f"message: {params.get('message')!r}")
    return {"received": sorted(params)}
```

This is the same format as the other scripts in the repository: a description, `PARAMETERS`, and `run(params)` returning a result for later steps. There is no extra flag, and the runner treats it exactly like any other script.

## What the starter does

Until you replace it, the starter prints one line per parameter with its name and value, and returns the names it received as `{"received": [...]}`. A script's `print()` output goes to the run's **User log**, so after a run you see, for example:

```text
device: 'ams01-acc-sw01'
message: 'Rebooted (soft) under CHG0031245'
```

That makes it easy to check that every parameter is wired the way you meant before writing the real work. Replace the lines under `# Replace this with your logic.` in the repository, through your normal review.

## Permission

Creating a script needs the **Write scripts** permission: the action `write_script` on the *Workflow* object type (`nautobot_workflow_engine.write_script_workflow`). See [Permissions](permissions.md).

Without it, the panel still builds the file from the step, but shows it as **The file it would save (read-only)** with **Copy code**, and says: *You need the “Write scripts” permission to create scripts. Copy the code and send it to an admin instead.*

![Without the permission the generated file is read-only, with Copy code](../assets/images/builder/15-new-script-read-only.png)

The repository must be ready for publishing, the same as for publishing a workflow. See [Getting a repository ready](publishing.md#getting-a-repository-ready).
