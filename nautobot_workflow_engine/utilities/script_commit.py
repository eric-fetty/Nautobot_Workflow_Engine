"""Commit one new script file to a workflow repository. The file is written, never run."""

import logging
from pathlib import Path

from nautobot_workflow_engine.models import WorkflowHelper
from nautobot_workflow_engine.schema.policy import script_path_allowed
from nautobot_workflow_engine.script_files import check_script
from nautobot_workflow_engine.utilities.git import acquire_repo_lock, plugin_setting, release_repo_lock
from nautobot_workflow_engine.utilities.git_publish import (
    PublishLog,
    author_actor,
    committer_actor,
    log_full_error,
    prepare_working_copy,
    publish_blocker,
    readable_failure,
)

logger = logging.getLogger(__name__)


class ScriptCommitError(Exception):
    """A plain reason the script was not created, and the HTTP status to answer with."""

    def __init__(self, status, message):
        """Keep the status and the message for people."""
        super().__init__(message)
        self.status = status
        self.message = message


def commit_script(*, user, repository, path, content):
    """Write ``path`` into ``repository``, commit as ``user``, push, and record it for the picker.

    Raises :class:`ScriptCommitError` with a plain message. Never imports or runs ``content``.
    """
    blocker = publish_blocker(repository)
    if blocker:
        raise ScriptCommitError(400, blocker.replace("Couldn't publish to", "Couldn't save the script to"))
    if not script_path_allowed(path):
        raise ScriptCommitError(400, "Scripts must live in the scripts folder.")
    problems = check_script(content)
    if problems:
        raise ScriptCommitError(400, problems[0]["message"])
    if WorkflowHelper.objects.filter(source_repo=repository, file_path=path).exists():
        raise ScriptCommitError(409, f"{path} already exists. Pick another name, or choose it in the list.")
    lock = acquire_repo_lock(repository.pk, int(plugin_setting("git_push_lock_timeout", 300)))
    if lock is None:
        raise ScriptCommitError(409, f"Another publish to {repository.name} is running. Try again in a moment.")
    log = PublishLog()
    try:
        helper = prepare_working_copy(repository, log)
        root = Path(helper.repo.working_tree_dir)
        target = root / path
        if target.exists():
            raise ScriptCommitError(409, f"{path} already exists. Pick another name, or choose it in the list.")
        message = (
            f"scripts: add {path} (starter)\n\nCreated in the workflow builder by {getattr(user, 'username', '')}.\n"
        )

        def write_and_commit():
            from git import Repo

            helper.repo = Repo(helper.repo.working_tree_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            helper.commit_with_added(message, author_actor(user), committer_actor())

        write_and_commit()
        helper.push_derived(write_and_commit, max_retries=3)
        repository.current_head = helper.head
        repository.save(update_fields=["current_head"])
    except ScriptCommitError:
        raise
    except Exception as exc:
        log_full_error(repository, exc, log)
        reason = readable_failure(exc, repository).replace("Couldn't push to", "Couldn't save the script to")
        raise ScriptCommitError(502, reason) from exc
    finally:
        release_repo_lock(lock)
    WorkflowHelper.objects.create(source_repo=repository, file_path=path, content=content)
    return path
