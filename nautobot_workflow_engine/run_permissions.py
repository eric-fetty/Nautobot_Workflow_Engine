"""Who may start and cancel workflow runs.

The ready-made **Run workflows** permission is one object permission on *Workflow* with the
actions ``view``, ``run`` and ``cancel``, so a constraint such as ``{"category": "Networking"}``
scopes all three together. Nautobot applies every action of a permission to every object type
it lists, so the permission can't also list *Workflow run* without handing out build rights
(``add``/``change`` on workflows) and every run's details (``view`` on runs).

The older model permissions still work for permissions admins made themselves:
``add_workflowrun`` plus ``view_workflow`` starts a run, and ``change_workflowrun`` cancels one.
"""

VIEW_WORKFLOW = "nautobot_workflow_engine.view_workflow"
RUN_WORKFLOW = "nautobot_workflow_engine.run_workflow"
CANCEL_WORKFLOW_RUNS = "nautobot_workflow_engine.cancel_workflow"
ADD_WORKFLOWRUN = "nautobot_workflow_engine.add_workflowrun"
CHANGE_WORKFLOWRUN = "nautobot_workflow_engine.change_workflowrun"


def can_run(user, workflow=None):
    """True when ``user`` may start ``workflow`` (or, with no workflow, at least one workflow)."""
    if workflow is None:
        return user.has_perm(RUN_WORKFLOW) or user.has_perm(ADD_WORKFLOWRUN)
    if not user.has_perm(VIEW_WORKFLOW, workflow):
        return False
    return user.has_perm(RUN_WORKFLOW, workflow) or user.has_perm(ADD_WORKFLOWRUN)


def can_cancel(user, workflow_run):
    """True when ``user`` may cancel this run.

    Any Scheduled or Pending Approval run of a workflow the person's Run workflows permission
    covers, whoever started it, the same reach the old ``change_workflowrun`` had.
    """
    if user.has_perm(CHANGE_WORKFLOWRUN, workflow_run):
        return True
    workflow = getattr(workflow_run, "workflow", None)
    return workflow is not None and user.has_perm(CANCEL_WORKFLOW_RUNS, workflow)
