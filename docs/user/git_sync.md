# Two-way Git sync

Nautobot-managed workflows live in the database. Git is kept equal to that database: a publish pushes a file, and a commit in Git is imported on the next sync. The later change wins. There is no screen that asks someone to pick a version.

Every workflow opens in the same editor. Git receives the published YAML. Drafts stay in the database.

## Setting up the repository

The repository needs **Provides** with **workflows** (`nautobot_workflow_engine.workflows`: scripts and workflow YAML) and a secrets group with an **HTTP(S)** / **Token** secret. Add an **HTTP(S)** / **Username** secret too when the Git server needs a username. The step-by-step setup, with screenshots, is in [Getting a repository ready](publishing.md#getting-a-repository-ready).

The token is put into the clone address the same way Nautobot's own repository sync does it (`get_repo_access_url`), so whatever already works for syncing that repository, and for the Golden Config app, works for publishing. It is never written to a log or to the workflow's Git error.

A repository that doesn't provide workflows, or has no token, can't be picked for publishing, and the builder names what is missing. A workflows repository without a token is still synced as a read-only source.

Older builds had a second provider, **Workflow publishing (Nautobot → Git)**. It is gone: the upgrade removes it from every repository, and repositories that had it keep publishing because they also provide workflows.

### Webhook secret

For the webhook below, add one more secret to the same group: access type **Generic**, secret type **Key**. That value is the HMAC secret (GitHub) or the token (GitLab). It is not the Git HTTP token.

## What gets committed

Publishing writes `<workflow_dir>/<key>.yaml` to the repository's configured branch (adopted workflows keep their existing path) with a managed-by header and canonical YAML. The author is the user who saved. The committer is the configured `git_committer_name`. The push goes directly to that branch. An empty commit is skipped. If the remote moved (a non-fast-forward), the job fetches, resets to the new tip, writes the files again, and pushes, up to three times. It never force-pushes and never rebases. If the branch doesn't exist yet and the repository is empty, the first publish creates it.

A failed push does not undo the publish. The indicator shows **Not synced · Retry** with the reason in plain words, and the push is retried automatically. The full error, without the token, goes to the server log and the publish job's log. Every message and its fix are listed in [When publishing fails](publishing.md#when-publishing-fails).

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

- A Nautobot-managed workflow that has never been published (`not_configured`, no content hash) is left unchanged. A YAML file with the same key does not replace a builder copy that was never pushed. After it has been published, latest-wins applies. A workflow owned by a different repository, or a Nautobot workflow with no repository, is still refused.
- A file whose bytes still match `git_content_hash` is our own push and does not create a revision.
- A valid change to an in-sync file becomes a published revision with source `git`, the commit author, and the SHA. The launch form reads that revision.
- Invalid YAML sets `git_invalid` and leaves the database definition unchanged.
- Deleting an in-sync file archives and disables the workflow. Runs are kept. Restore publishes the file again. Restoring moves `published_at` to the restore time, so that database save is newer than the commit that removed the file.
- An unpushed database save is compared with the committer time of the Git commit (`T_git`) and `published_at` (`T_db`). The later one wins. When the database wins, the commit records `Nautobot-Overwrote`.

Clocks: `T_git` is the Git committer date and `T_db` is the database clock. Skew only matters for edits that land at nearly the same moment. The stale-edit check blocks a save that started from an older copy, including "Updated in Git by <author> at <time>; reload to continue."

## Duplicate

**Duplicate** on the Workflows list copies a workflow into a disabled draft. It does not publish. The first **Publish changes** writes the canonical YAML to that workflow's repository.
