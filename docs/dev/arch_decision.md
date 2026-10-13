# Architecture

## Data Flow

1. `datasources.py` imports workflow YAML, scripts, helpers, and custom filters from
    Nautobot repository content into the app's database models.
2. `forms.py` creates input fields and resolves validation, visibility, and dependent
    object choices. Views validate submissions and create workflow runs.
3. `executors.py` renders action parameters and runs ordered Nautobot or AWX actions.
    Earlier action results are available to subsequent actions.
4. `tasks.py` monitors external jobs. `scheduling.py`, `jobs.py`, and `servicenow.py`
    coordinate deferred execution and optional change-window validation.
5. Views, tables, and templates expose workflow status, history, inputs, and logs.

## Boundaries to Preserve

- Workflow source synchronization and execution are separate operations. A workflow
  definition must synchronize successfully before it can be launched.
- Action order is persisted within each phase. Do not rely on identifier order.
- Scripts, helpers, and filters are trusted executable code. The app is not a sandbox.
- Script parameters, results, diagnostics, and output are intentionally logged.
- Web and worker environments must share app versions, configuration, and dependencies.

Schema evolution belongs in the app's migration history. Keep historical migrations
when cleaning up source or packaging, and use the existing ordering, executor, form,
filter, and scheduling tests when changing these contracts.
