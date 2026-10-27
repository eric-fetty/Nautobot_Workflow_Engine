"""Commit and push workflow files. A non-fast-forward push is re-derived, never rebased."""

import re
from hashlib import sha256

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from git import Actor, Repo
from git.exc import GitCommandError
from nautobot.core.utils.git import GIT_ENVIRONMENT, BranchDoesNotExist, GitRepo
from nautobot.core.utils.logging import sanitize

_USERINFO = re.compile(r"(//)([^/\s@]+)@")

NON_FAST_FORWARD_MARKERS = (
    "non-fast-forward",
    "non fast-forward",
    "failed to push some refs",
    "fetch first",
)


class NonFastForward(GitCommandError):
    """The remote rejected the push because it is not a fast-forward."""

    def __init__(self, message):
        """Store the remote's rejection text."""
        super().__init__(["git", "push"], message)
        self.message = message


def is_non_fast_forward(message):
    """Return True when ``message`` is a non-fast-forward rejection."""
    text = (message or "").lower()
    return any(marker in text for marker in NON_FAST_FORWARD_MARKERS)


class WorkflowGitRepo(GitRepo):
    """A local clone that commits as a named author and recommit after a rejected push."""

    def __init__(self, path, url, clone_initially=False, branch=None, depth=0, nautobot_repo_obj=None):
        """Open or initialize ``path`` without logging the credential-bearing URL."""
        self.nautobot_repo_obj = nautobot_repo_obj
        self.branch_name = branch
        super().__init__(path, url, clone_initially=clone_initially, branch=branch, depth=depth)

    def commit_with_added(self, message, author, committer):
        """Stage every change and commit. Return False when there is nothing to commit."""
        with self.repo.git.custom_environment(**GIT_ENVIRONMENT):
            self.repo.git.add(A=True)
        if not self._index_differs_from_head():
            return False
        self.repo.index.commit(message, author=author, committer=committer)
        return True

    def _index_differs_from_head(self):
        try:
            valid = self.repo.head.is_valid()
        except (ValueError, GitCommandError):
            valid = False
        if not valid:
            return bool(self.repo.index.entries)
        return self.repo.is_dirty(index=True, working_tree=True, untracked_files=True)

    def push_once(self, branch):
        """Push HEAD to ``branch``. Raise :class:`NonFastForward` when the remote is ahead."""
        refspec = f"HEAD:refs/heads/{branch}"
        try:
            with self.repo.git.custom_environment(**GIT_ENVIRONMENT):
                infos = list(self.repo.remotes.origin.push(refspec=refspec))
        except GitCommandError as exc:
            message = str(exc)
            lowered = message.lower()
            protected = any(word in lowered for word in ("protected", "denied", "pre-receive", "hook declined"))
            if not protected and is_non_fast_forward(message):
                raise NonFastForward(message) from exc
            raise
        problems = []
        non_fast_forward = False
        for info in infos:
            flags = info.flags
            failed = flags & (info.ERROR | info.REJECTED | info.REMOTE_REJECTED | info.REMOTE_FAILURE)
            if not failed:
                continue
            summary = info.summary or str(info.flags)
            problems.append(summary)
            # `!` sets ERROR for every rejection, including a non-fast-forward.
            # A hook or protected branch sets REMOTE_REJECTED or REMOTE_FAILURE.
            remote_policy = flags & (info.REMOTE_REJECTED | info.REMOTE_FAILURE)
            if not remote_policy and (flags & info.REJECTED or is_non_fast_forward(summary)):
                non_fast_forward = True
        if not problems:
            return True
        message = "; ".join(problems)
        if non_fast_forward:
            raise NonFastForward(message)
        raise GitCommandError(["git", "push"], message)

    def push_derived(self, regenerate, max_retries=3):
        """Push, and on a non-fast-forward rejection reset to the new tip and commit again.

        ``regenerate`` re-reads the database, rewrites files, and commits. This never rebases.
        """
        branch = self.branch_name or getattr(self.nautobot_repo_obj, "branch", None) or "main"
        attempts = 0
        while True:
            try:
                self.push_once(branch)
                return True
            except NonFastForward:
                attempts += 1
                if attempts > max_retries:
                    raise
                self._reset_to_remote(branch)
                regenerate()
            except GitCommandError:
                raise

    def _reset_to_remote(self, branch):
        """Fetch and hard-reset the local branch to the remote tip."""
        from nautobot.extras.datasources.git import ensure_git_repository

        if self.nautobot_repo_obj is not None:
            try:
                ensure_git_repository(self.nautobot_repo_obj)
                self.repo = Repo(self.repo.working_dir)
                return
            except BranchDoesNotExist:
                pass
        with self.repo.git.custom_environment(**GIT_ENVIRONMENT):
            self.repo.remotes.origin.fetch()
            self.repo.git.checkout("-B", branch, f"origin/{branch}")


def actor(name, email):
    """Return a GitPython author or committer."""
    return Actor(name or "Nautobot", email or "nautobot-workflow-engine@localhost")


def plugin_setting(name, default=None):
    """Return one workflow-engine setting (nautobot_config.py first, then the Settings page)."""
    from ..settings_helpers import get_setting

    return get_setting(name, default)


def lock_key(repository_id):
    """Cache key that serializes pushes and probe fetches for one repository."""
    return f"nwe:git:{repository_id}"


def acquire_repo_lock(repository_id, timeout):
    """Take the repository lock. Return the cache key, or None when it is already held."""
    key = lock_key(repository_id)
    wait = max(int(timeout or 0), 0)
    if cache.add(key, "1", timeout=max(wait, 1)):
        return key
    if wait <= 0:
        return None
    import time

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if cache.add(key, "1", timeout=max(wait, 1)):
            return key
        time.sleep(0.05)
    return None


def release_repo_lock(key):
    """Release a lock acquired by :func:`acquire_repo_lock`."""
    if key:
        cache.delete(key)


def sanitize_git_error(exc, secrets=()):
    """Return an error string that does not contain a token or userinfo."""
    text = sanitize(str(exc))
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "****")
    return _USERINFO.sub(r"\1****@", text)


def scrub_plain(text, secrets=()):
    """Strip secret values and URL userinfo from a message written for people.

    Unlike :func:`sanitize_git_error`, this keeps words like "secrets group" readable.
    """
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "****")
    return _USERINFO.sub(r"\1****@", text)


def file_sha256(payload):
    """Return the hex sha256 of file bytes."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return sha256(payload or b"").hexdigest()


def absolute_url(path):
    """Prefix a path with the current site, when one is configured."""
    base = getattr(settings, "EXTERNAL_URL", "") or ""
    if not base:
        try:
            from django.contrib.sites.models import Site

            base = f"http://{Site.objects.get_current().domain}"
        except Exception:
            base = "http://localhost"
    return base.rstrip("/") + path


def builder_url(workflow):
    """Absolute URL of the workflow in the builder."""
    path = reverse("plugins:nautobot_workflow_engine:workflow_builder_edit", kwargs={"key": workflow.key})
    return absolute_url(path)


def revision_url(workflow, number):
    """Absolute URL of one revision."""
    path = reverse(
        "plugins-api:nautobot_workflow_engine-api:workflow-revision",
        kwargs={"key": workflow.key, "number": number},
    )
    return absolute_url(path)
