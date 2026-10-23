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
6. On Nautobot 3.2+, `approvals.py` reconciles scoped `ApprovalWorkflowDefinition`
    shells during git sync when `approval_required` is set. Approval-gated immediate
    launches create a near-term `ScheduledJob` (status `pending_approval`) instead of
    the Celery immediate path so Nautobot's approval pipeline can apply. Non-approval
    workflows continue to use Celery immediate execution.

## Builder and Git publishing

- `views.py` serves the builder page and its config. The builder itself is the ES
  module `static/.../js/builder/studio.js`, with logic shared with the launch page in
  `js/shared/` (`conditions.js`, `issues.js`, `template.js`, `validate.js`, `csv.js`).
  Pure logic in those modules is covered by `node --test ui-tests/`.
- `api/views.py` holds the builder API: draft save and validate, Preview run, publish,
  Git status and retry, and `builder/script-preview/` and `builder/scripts/` for new
  scripts.
- `services.py` owns the draft/published revision lifecycle and permission checks.
  `schema/` holds the one JSON Schema, validation and policy (model allowlist,
  `scripts_dir`, the code permission).
- `utilities/git_publish.py` publishes: `publish_blocker()` says in plain words why a
  repository can't take a publish, `readable_failure()` turns a Git error into one
  sentence, and `log_full_error()` writes the sanitized full error to the server log
  and the job log. `utilities/git.py` does the push with non-fast-forward retries and
  token scrubbing; credentials come from core `get_repo_access_url()`.
- `script_files.py` renders and checks builder-created scripts with `ast` only.
  `utilities/script_commit.py` commits them with the same lock and credentials as
  publish. `executors.py` refuses a script whose `ENABLED` is `False`, checked by
  parsing before import.

## Boundaries to Preserve

- The builder never executes anything: Preview run renders in the sandbox, script
  parameters are read with `ast`, and builder-created scripts are parsed, never imported.
- Messages shown to people are plain sentences. Keep raw Git output and codes in the
  server and job logs, and never put a token in either.

- Workflow source synchronization and execution are separate operations. A workflow
  definition must synchronize successfully before it can be launched.
- Action order is persisted within each phase. Do not rely on identifier order.
- Scripts, helpers, and filters are trusted executable code. The app is not a sandbox.
- Script parameters, results, diagnostics, and output are intentionally logged.
- Web and worker environments must share app versions, configuration, and dependencies.

Schema evolution belongs in the app's migration history. Keep historical migrations
when cleaning up source or packaging, and use the existing ordering, executor, form,
filter, and scheduling tests when changing these contracts.
