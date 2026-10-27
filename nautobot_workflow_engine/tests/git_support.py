"""Local bare-repo fixtures for two-way Git tests."""

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.datasources.git import GitRepoInfo
from nautobot.extras.models import GitRepository, Secret, SecretsGroup, SecretsGroupAssociation
from nautobot.users.models import ObjectPermission

from nautobot_workflow_engine.models import (
    GIT_STATUS_IN_SYNC,
    GIT_STATUS_NOT_SYNCED,
    GIT_STATUS_PENDING,
    WORKFLOWS_CONTENT,
    Workflow,
)
from nautobot_workflow_engine.services import publish as publish_workflow
from nautobot_workflow_engine.utilities.git_publish import PublishLog, prepare_working_copy, publish_repository

User = get_user_model()
_PERM_SEQ = 0
TOKEN_ENV = "NWE_TEST_GIT_TOKEN"
TOKEN_VALUE = "test-token-not-for-logs"


def grant(user, actions, model, constraints=None):
    """Grant one object permission."""
    global _PERM_SEQ
    _PERM_SEQ += 1
    permission = ObjectPermission.objects.create(
        name=f"git-{_PERM_SEQ}-{user.username}-{'-'.join(actions)}",
        actions=list(actions),
        constraints=constraints,
        enabled=True,
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    if hasattr(user, "_object_perm_cache"):
        del user._object_perm_cache
    return permission


class BareRepoMixin:
    """A real ``git init --bare`` remote and a file:// checkout URL."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bare = root / "origin.git"
        self.git_root = root / "checkouts"
        self.git_root.mkdir()
        subprocess.run(["git", "init", "--bare", str(self.bare)], check=True)
        subprocess.run(
            ["git", "--git-dir", str(self.bare), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )
        subprocess.run(["git", "config", "--global", "protocol.file.allow", "always"], check=False)
        subprocess.run(["git", "config", "--global", "safe.directory", "*"], check=False)
        self.settings_override = override_settings(GIT_ROOT=str(self.git_root))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        os.environ[TOKEN_ENV] = TOKEN_VALUE
        self.addCleanup(lambda: os.environ.pop(TOKEN_ENV, None))
        self.user = User.objects.create(
            username=f"git-author-{uuid.uuid4().hex[:8]}",
            email="ada@example.com",
            first_name="Ada",
            last_name="Lovelace",
        )
        grant(self.user, ["view", "add", "change", "delete", "publish"], Workflow)
        self.repo = self.make_repository()
        grant(self.user, ["view"], GitRepository)
        self.url_patch = patch(
            "nautobot.extras.datasources.git.get_repo_from_url_to_path_and_from_branch",
            side_effect=self.repo_info,
        )
        self.url_patch.start()
        self.addCleanup(self.url_patch.stop)

    def repo_info(self, record):
        """Point core's clone helper at the local bare repo."""
        return GitRepoInfo(self.bare.as_uri(), record.filesystem_path, record.branch or "main")

    def make_repository(self, *, publishable=True):
        """Create a Git repository row whose secret is an environment variable."""
        token = Secret.objects.create(
            name=f"token-{uuid.uuid4().hex[:8]}",
            provider="environment-variable",
            parameters={"variable": TOKEN_ENV},
        )
        group = SecretsGroup.objects.create(name=f"grp-{uuid.uuid4().hex[:8]}")
        SecretsGroupAssociation.objects.create(
            secrets_group=group,
            secret=token,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN,
        )
        # A repository that provides workflows and has a token can be published to.
        # Without the token it stays a read-only workflow source.
        return GitRepository.objects.create(
            name=f"Publish {uuid.uuid4().hex[:8]}",
            remote_url=f"https://example.invalid/wf-{uuid.uuid4().hex[:6]}.git",
            branch="main",
            secrets_group=group if publishable else None,
            provided_contents=[WORKFLOWS_CONTENT],
        )

    def definition(self, key="lab-flow", **overrides):
        """A small valid workflow definition."""
        name = overrides.pop("name", "Lab flow")
        data = {
            "key": key,
            "name": name,
            "category": "lab",
            "inputs": [{"key": "hostname", "label": "Hostname", "type": "string"}],
            "actions": [{"type": "awx", "name": "run", "template_id": 3}],
        }
        data.update(overrides)
        return data

    def save_and_push(self, definition, workflow=None, base_revision=None, enable=False, summary="Ship it"):
        """Publish in the database, then push to the bare repo."""

        def _publish():
            return publish_workflow(
                user=self.user,
                workflow=workflow,
                definition=definition,
                base_revision=base_revision,
                enable=enable,
                repository=self.repo,
                commit_summary=summary,
            )

        capture = getattr(self, "captureOnCommitCallbacks", None)
        if capture is not None:
            with capture(execute=True):
                saved, revision = _publish()
        else:
            saved, revision = _publish()
        saved.refresh_from_db()
        if saved.git_status != GIT_STATUS_IN_SYNC:
            if saved.git_status not in (GIT_STATUS_PENDING, GIT_STATUS_NOT_SYNCED):
                saved.git_status = GIT_STATUS_PENDING
                saved.git_path = saved.git_path or f"workflows/{saved.key}.yaml"
                saved.save(update_fields=["git_status", "git_path", "last_updated"])
            publish_repository(self.repo.pk, self.user)
            saved.refresh_from_db()
        return saved, revision

    def git(self, *args, cwd=None, env=None):
        """Run git and return stdout."""
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    def head(self):
        """Current bare-repo tip."""
        return self.git("--git-dir", str(self.bare), "rev-parse", "HEAD").strip()

    def show_file(self, path, rev="HEAD"):
        """Return one file from the bare repo."""
        return self.git("--git-dir", str(self.bare), "show", f"{rev}:{path}")

    def commit_count(self):
        """Number of commits on main."""
        return int(self.git("--git-dir", str(self.bare), "rev-list", "--count", "HEAD").strip())

    def tip_fields(self):
        """Author, committer, and subject of HEAD."""
        text = self.git(
            "--git-dir",
            str(self.bare),
            "log",
            "-1",
            "--format=%an%x1f%ae%x1f%cn%x1f%ce%x1f%s",
        ).strip()
        author, email, committer, committer_email, subject = text.split("\x1f")
        body = self.git("--git-dir", str(self.bare), "log", "-1", "--format=%B")
        return {
            "author": author,
            "email": email,
            "committer": committer,
            "committer_email": committer_email,
            "subject": subject,
            "body": body,
        }

    def remote_commit(self, files, message, *, author="Git Editor", when=None, remove=()):
        """Commit ``files`` in a second clone and push them to the bare repo."""
        clone = Path(self.tmp.name) / f"clone-{uuid.uuid4().hex[:8]}"
        self.git("clone", self.bare.as_uri(), str(clone))
        for relative, content in files.items():
            path = clone / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = content if isinstance(content, bytes) else content.encode()
            path.write_bytes(payload)
        for relative in remove:
            target = clone / relative
            if target.exists():
                target.unlink()
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": author,
                "GIT_AUTHOR_EMAIL": "editor@example.com",
                "GIT_COMMITTER_NAME": author,
                "GIT_COMMITTER_EMAIL": "editor@example.com",
            }
        )
        if when:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        subprocess.run(["git", "add", "-A"], cwd=clone, check=True, env=env)
        subprocess.run(["git", "commit", "-m", message], cwd=clone, check=True, env=env)
        subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=clone, check=True, env=env)
        return clone

    def pull_into_database(self):
        """Fetch the bare repo and run the workflow sync against that checkout."""
        from nautobot_workflow_engine.datasources import sync_workflows_from_repo

        log = PublishLog()
        prepare_working_copy(self.repo, log)
        self.repo.refresh_from_db()
        sync_workflows_from_repo(self.repo, log, delete=False)
        return log
