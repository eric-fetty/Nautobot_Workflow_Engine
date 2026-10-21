# Two-way Git sync

Nautobot-managed workflows live in the database. Git is kept equal to that database: a publish pushes a file, and a commit in Git is imported on the next sync. The later change wins. There is no screen that asks someone to pick a version.

Git-managed workflows stay read-only in the builder until someone adopts them.

## Secrets group

The repository needs a secrets group before it can be published to.

1. Create a secret whose provider returns the Git HTTP token.
2. If the host requires a username, create a second secret for it.
3. Create a secrets group and associate them:
   - Access type **HTTP**, secret type **Token** for the token.
   - Access type **HTTP**, secret type **Username** when a username is required.
4. For the webhook below, associate a third secret: access type **Generic**, secret type **Key**. That value is the HMAC secret (GitHub) or the token (GitLab). It is not the Git HTTP token.
5. Assign the secrets group to the Git repository.

The token is injected into the clone URL the way Nautobot core does it. It is not written to the job log or to `git_error`. A misconfigured secret raises `SecretError` to the caller; the database save is kept and the workflow shows **Not synced to Git**.

## Opt in

Publishing requires both provided contents:

- `nautobot_workflow_engine.workflows` (scripts and Git-managed YAML)
- `nautobot_workflow_engine.workflow_publish` (Nautobot → Git)

A repository missing either one, or missing the HTTP token, is shown as read-only and cannot be selected for publish.

## What gets committed

Publishing writes `<workflow_dir>/<key>.yaml` (adopted workflows keep their existing path) with a managed-by header and canonical YAML. The author is the user who saved. The committer is the configured `git_committer_name`. The push goes directly to the repository branch. An empty commit is skipped. If the remote moved, the job fetches, resets to the new tip, writes the files again, and pushes. It does not rebase.

A failed push does not undo the save. The badge stays **Not synced to Git**, with the next retry time and **Retry now**.

## Refresh, webhook, and the schedule

Opening the builder runs `git ls-remote`. When the remote is ahead, the app syncs that repository before the editor loads. If Git cannot be reached, the editor still opens and says: "Couldn't check Git for updates: <reason>. Your save will still be checked."

Recommended: a webhook.

- URL: `POST /api/plugins/workflow-engine/git/webhook/<repository-uuid>/`
- GitHub: set the secret to the Generic/Key value and send `X-Hub-Signature-256`.
- GitLab: set the same value as the webhook token (`X-Gitlab-Token`).
- Only `push` events for the configured branch enqueue a sync. Other events return 202 and are ignored. A bad signature returns 403. A repository with no webhook secret returns 404.

Without the app endpoint, a CI job can call core `POST /api/extras/git-repositories/<id>/sync/` with a Nautobot token.

`Workflow Git Maintenance` also runs on a schedule (default every 5 minutes). It enqueues a sync when `ls-remote` differs from `current_head`, and it retries workflows whose `git_next_retry_at` is due. The backoff is 1, 5, 15, then 60 minutes.

## Latest change wins

- A Nautobot-managed workflow that has never been published (`not_configured`, no content hash) is left unchanged. A YAML file with the same key does not replace a builder copy that was never pushed. After it has been published, latest-wins applies. A workflow owned by a different repository, or a Nautobot workflow with no repository, is still refused with E-NWE-101.
- A file whose bytes still match `git_content_hash` is our own push and does not create a revision.
- A valid change to an in-sync file becomes a published revision with source `git`, the commit author, and the SHA. The launch form reads that revision.
- Invalid YAML sets `git_invalid` and leaves the database definition unchanged.
- Deleting an in-sync file archives and disables the workflow. Runs are kept. Restore publishes the file again. Restoring moves `published_at` to the restore time, so that database save is newer than the commit that removed the file.
- An unpushed database save is compared with the committer time of the Git commit (`T_git`) and `published_at` (`T_db`). The later one wins. When the database wins, the commit records `Nautobot-Overwrote`.

Clocks: `T_git` is the Git committer date and `T_db` is the database clock. Skew only matters for edits that land at nearly the same moment. The stale-edit check blocks a save that started from an older copy, including "Updated in Git by <author> at <time>; reload to continue."

## Adopt and duplicate

**Adopt** keeps the key, runs, approval definition, and path, then the next push rewrites the file in canonical form. If the file contains `{% include %}`, the dialog lists those paths and warns that future saves inline them.

**Duplicate** copies the workflow into a disabled Nautobot-managed draft. It does not publish.
