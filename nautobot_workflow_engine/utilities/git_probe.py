"""Read a workflow file from the remote without merging it into the editor."""

import os
import subprocess
from hashlib import sha256
from pathlib import Path

from django.conf import settings
from git import Repo
from git.exc import GitCommandError
from nautobot.core.utils.git import GIT_ENVIRONMENT

from nautobot_workflow_engine.utilities.git import acquire_repo_lock, plugin_setting, release_repo_lock


class GitProbeError(Exception):
    """The remote could not be read. The message is already safe to show."""


class PathState:
    """What the remote branch currently has for one file."""

    def __init__(self, head, blob_hash, author, committed_at, commit, present):
        """Store the remote head, blob hash, and the commit that last touched the path."""
        self.head = head or ""
        self.blob_hash = blob_hash or ""
        self.author = author or ""
        self.committed_at = committed_at
        self.commit = commit or ""
        self.present = present


class GitRemoteProbe:
    """``ls-remote`` plus a throwaway clone used only to read one blob."""

    def __init__(self, repository):
        """Remember which repository to read."""
        self.repository = repository

    def remote_head(self):
        """Return the tip commit of the configured branch, or '' when the branch is absent."""
        url = self._url()
        branch = self.repository.branch or "main"
        timeout = int(plugin_setting("git_remote_timeout_seconds", 10))
        try:
            completed = subprocess.run(  # noqa: S603
                ["git", "ls-remote", url, f"refs/heads/{branch}"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitProbeError("Timed out contacting Git.") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Git could not be contacted.").strip()
            raise GitProbeError(detail.splitlines()[-1][:300])
        line = (completed.stdout or "").strip().splitlines()
        if not line:
            return ""
        return line[0].split()[0]

    def read_path(self, relative_path):
        """Fetch the probe clone and return the blob hash and the last commit that touched the path."""
        lock = acquire_repo_lock(self.repository.pk, int(plugin_setting("git_remote_timeout_seconds", 10)) or 1)
        if lock is None:
            raise GitProbeError("The repository is busy.")
        try:
            head = self.remote_head()
            repo = self._probe_repo()
            return self._state(repo, relative_path, head)
        finally:
            release_repo_lock(lock)

    def file_state(self, relative_path):
        """Return head, blob hash, author, and commit time for ``relative_path``."""
        head = self.remote_head()
        if not head:
            return PathState("", "", "", None, "", False)
        return self.read_path(relative_path)

    def _url(self):
        from nautobot.extras.datasources.git import get_repo_from_url_to_path_and_from_branch

        return get_repo_from_url_to_path_and_from_branch(self.repository).from_url

    def _probe_repo(self):
        root = Path(settings.GIT_ROOT) / ".nwe-probe" / str(self.repository.pk)
        root.mkdir(parents=True, exist_ok=True)
        url = self._url()
        branch = self.repository.branch or "main"
        if (root / ".git").is_dir():
            repo = Repo(root)
            if url not in repo.remotes.origin.urls:
                repo.remotes.origin.set_url(url)
        else:
            repo = Repo.init(root)
            repo.create_remote("origin", url=url)
        with repo.git.custom_environment(**GIT_ENVIRONMENT):
            try:
                repo.remotes.origin.fetch()
            except GitCommandError:
                return repo
            if branch in repo.remotes.origin.refs:
                repo.git.checkout("-B", branch, f"origin/{branch}")
        return repo

    def _state(self, repo, relative_path, head):
        from django.utils.dateparse import parse_datetime

        present = False
        blob_hash = ""
        try:
            node = repo.head.commit.tree
            for part in Path(relative_path).parts:
                node = node[part]
            blob_hash = sha256(node.data_stream.read()).hexdigest()
            present = True
        except (GitCommandError, KeyError, ValueError, AttributeError):
            present = False
        author = ""
        commit = ""
        moment = None
        try:
            text = repo.git.log("-1", "--format=%H%x00%an%x00%cI", "--", relative_path)
        except GitCommandError:
            text = ""
        if text.strip():
            commit, author, raw_when = (text.split("\x00") + ["", "", ""])[:3]
            moment = parse_datetime(raw_when)
        if not commit:
            commit = head
        return PathState(head, blob_hash, author, moment, commit, present)
