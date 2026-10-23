# Git and the builder

Git receives published versions only. A draft stays in the Nautobot database and is visible to editors in the builder. People running a workflow keep the published version until the next publish.

## What you see

There is one **Workflows** list. Each row has **Run**, **Edit**, and a menu for versions, a copy, run history, the YAML, and delete. **Enabled** is a switch on that row and in the builder. It works only after the first publish.

Git shows up in three places (all states are listed in [Publishing and versions](publishing.md#git)):

- **Synced** when the published file matches the repository.
- **Not synced · Retry** when a push failed. Hover over it for the reason in plain words. The version is already live in Nautobot. Retry runs the push again, and Nautobot also retries on its own. See [When publishing fails](publishing.md#when-publishing-fails).
- A banner when you open the builder and Git has a newer published version than the draft you started from: **Updated in Git by &lt;author&gt; at &lt;time&gt;**. Publish stays off until you compare or load that version. Loading it copies it into the draft. The draft you had is kept in Versions and does not go live by itself.

Opening the builder pulls Git first. A save of the draft is allowed while Git is ahead. Publish is not.

## Import

A file that has never been seen sets **Enabled** from its `enabled:` key, or on when the key is missing. Later syncs update the definition and leave the switch where an editor set it.

`help_text`, `placeholder`, `min_value`, `max_value`, `min_items`, `max_items`, `min_rows`, `max_rows`, and `csv_import` are optional. Older files that omit them keep working. A step `label` is written only when it differs from `name`.
