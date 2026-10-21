"""Push and pull against a local bare repository."""

import subprocess
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from git.exc import GitCommandError
from nautobot.extras.secrets.exceptions import SecretError

from nautobot_workflow_engine.models import (
    GIT_STATUS_IN_SYNC,
    GIT_STATUS_NOT_SYNCED,
    GIT_STATUS_PENDING,
    Workflow,
    WorkflowRevision,
    WorkflowRun,
)
from nautobot_workflow_engine.services import adopt_workflow
from nautobot_workflow_engine.services import publish as publish_workflow
from nautobot_workflow_engine.tests.git_support import TOKEN_VALUE, BareRepoMixin
from nautobot_workflow_engine.utilities.git import WorkflowGitRepo, lock_key
from nautobot_workflow_engine.utilities.git_publish import next_retry_at, publish_repository, run_git_maintenance


class GitSyncTestCase(BareRepoMixin, TestCase):
    """Real commits to a bare repo. The credential URL is patched to file://."""

    def test_first_push_records_author_committer_and_status(self):
        saved, revision = self.save_and_push(self.definition())
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC, saved.git_error)
        self.assertEqual(saved.git_commit, self.head())
        path = saved.git_path or "workflows/lab-flow.yaml"
        text = self.show_file(path)
        self.assertIn("# Managed by Nautobot Workflow Engine:", text)
        self.assertIn("latest change wins", text)
        self.assertIn("key: lab-flow", text)
        tip = self.tip_fields()
        self.assertEqual(tip["author"], "Ada Lovelace")
        self.assertEqual(tip["email"], "ada@example.com")
        self.assertEqual(tip["committer"], "Nautobot Workflow Engine")
        self.assertEqual(tip["committer_email"], "nautobot-workflow-engine@localhost")
        self.assertIn("workflow(lab-flow): publish r1", tip["subject"])
        self.assertIn("Ship it", tip["subject"])
        self.assertLessEqual(len(tip["subject"]), 72)
        self.assertIn(f"Nautobot-Workflow: {saved.key}", tip["body"])
        self.assertIn(f"Nautobot-Revision: {revision.pk}", tip["body"])
        self.assertIn("Nautobot-User: " + self.user.username, tip["body"])
        self.assertIn("Nautobot-Url:", tip["body"])
        self.assertIn("Nautobot-Job-Result:", tip["body"])
        self.assertNotIn(TOKEN_VALUE, tip["body"])
        self.assertNotIn(TOKEN_VALUE, saved.git_error)

    def test_second_push_with_no_edits_does_not_add_a_commit(self):
        self.save_and_push(self.definition())
        before = self.commit_count()
        publish_repository(self.repo.pk, self.user)
        self.assertEqual(self.commit_count(), before)

    def test_archive_removes_the_file_and_keeps_runs(self):
        saved, _revision = self.save_and_push(self.definition(), enable=False)
        run = WorkflowRun.objects.create(workflow=saved, requested_by=self.user, inputs={"hostname": "leaf-01"})
        saved.archived = True
        saved.enabled = False
        saved.git_status = GIT_STATUS_PENDING
        saved.save(update_fields=["archived", "enabled", "git_status", "last_updated"])
        publish_repository(self.repo.pk, self.user)
        saved.refresh_from_db()
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC, saved.git_error)
        with self.assertRaises(subprocess.CalledProcessError):
            self.show_file(saved.git_path)
        self.assertTrue(WorkflowRun.objects.filter(pk=run.pk).exists())

    def test_yaml_enabled_does_not_flip_the_switch_after_import(self):
        saved, _revision = self.save_and_push(self.definition(), enable=True)
        saved.enabled = False
        saved.save(update_fields=["enabled"])
        text = self.show_file(saved.git_path).replace("name: Lab flow", "name: Still off")
        if "enabled:" not in text:
            text = text.replace("key: lab-flow\n", "key: lab-flow\nenabled: true\n", 1)
        self.remote_commit({saved.git_path: text}, "toggle in yaml", author="Git Editor")
        self.pull_into_database()
        saved.refresh_from_db()
        self.assertFalse(saved.enabled)
        self.assertEqual(saved.name, "Still off")

    def test_git_change_becomes_a_published_revision(self):
        saved, revision = self.save_and_push(self.definition())
        before = saved.revisions.count()
        text = self.show_file(saved.git_path).replace("name: Lab flow", "name: From Git")
        self.remote_commit({saved.git_path: text}, "edit in git", author="Git Editor")
        self.pull_into_database()
        saved.refresh_from_db()
        self.assertEqual(saved.name, "From Git")
        self.assertEqual(saved.managed_by, "nautobot")
        self.assertEqual(saved.revisions.count(), before + 1)
        newest = saved.published_revision
        self.assertEqual(newest.source, WorkflowRevision.SOURCE_GIT)
        self.assertEqual(newest.git_author, "Git Editor")
        self.assertEqual(newest.git_commit, self.head())
        self.assertNotEqual(newest.pk, revision.pk)
        self.assertEqual(saved.inputs.get().label, "Hostname")

    def test_own_push_does_not_create_another_revision(self):
        saved, _revision = self.save_and_push(self.definition())
        before = saved.revisions.count()
        self.pull_into_database()
        saved.refresh_from_db()
        self.assertEqual(saved.revisions.count(), before)
        self.assertEqual(saved.name, "Lab flow")

    def test_invalid_git_file_leaves_the_database_unchanged(self):
        saved, _revision = self.save_and_push(self.definition())
        self.remote_commit({saved.git_path: "name: broken\n"}, "break the file", author="Git Editor")
        log = self.pull_into_database()
        saved.refresh_from_db()
        self.assertEqual(saved.git_status, "git_invalid")
        self.assertIn("E-NWE-103", "\n".join(log.lines))
        self.assertEqual(saved.name, "Lab flow")
        self.assertEqual(saved.raw_definition["name"], "Lab flow")

    def test_deleting_the_file_archives_and_restore_publishes_it(self):
        saved, _revision = self.save_and_push(self.definition())
        run = WorkflowRun.objects.create(workflow=saved, requested_by=self.user, inputs={})
        self.remote_commit({}, "delete workflow", remove=[saved.git_path])
        self.pull_into_database()
        saved.refresh_from_db()
        self.assertTrue(saved.archived)
        self.assertFalse(saved.enabled)
        self.assertTrue(WorkflowRun.objects.filter(pk=run.pk).exists())
        from nautobot_workflow_engine.services import unarchive_workflow

        unarchive_workflow(user=self.user, workflow=saved)
        saved.refresh_from_db()
        saved.git_status = GIT_STATUS_PENDING
        saved.save(update_fields=["git_status", "last_updated"])
        publish_repository(self.repo.pk, self.user)
        saved.refresh_from_db()
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC, saved.git_error)
        self.assertIn("key: lab-flow", self.show_file(saved.git_path))

    def test_newer_git_supersedes_the_unpushed_save(self):
        saved, revision = self.save_and_push(self.definition())
        text = self.show_file(saved.git_path).replace("name: Lab flow", "name: From Git")
        self.remote_commit(
            {saved.git_path: text},
            "future git edit",
            author="Git Editor",
            when="2030-01-01T00:00:00+00:00",
        )
        definition = dict(revision.definition)
        definition["name"] = "From DB"
        revision.definition = definition
        revision.published_at = timezone.now()
        revision.save(update_fields=["definition", "published_at"])
        saved.git_status = GIT_STATUS_PENDING
        saved.name = "From DB"
        saved.save(update_fields=["git_status", "name", "last_updated"])
        publish_repository(self.repo.pk, self.user)
        saved.refresh_from_db()
        self.assertEqual(saved.name, "From Git", saved.git_error)
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC)
        self.assertEqual(saved.published_revision.source, WorkflowRevision.SOURCE_GIT)

    def test_newer_database_overwrites_git(self):
        saved, revision = self.save_and_push(self.definition())
        text = self.show_file(saved.git_path).replace("name: Lab flow", "name: Old Git")
        self.remote_commit(
            {saved.git_path: text},
            "old git edit",
            author="Git Editor",
            when="2020-01-01T00:00:00+00:00",
        )
        overwritten = self.head()
        definition = dict(revision.definition)
        definition["name"] = "From DB"
        revision.definition = definition
        revision.published_at = timezone.now()
        revision.save(update_fields=["definition", "published_at"])
        saved.git_status = GIT_STATUS_PENDING
        saved.name = "From DB"
        saved.save(update_fields=["git_status", "name", "last_updated"])
        publish_repository(self.repo.pk, self.user)
        saved.refresh_from_db()
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC, saved.git_error)
        self.assertIn("name: From DB", self.show_file(saved.git_path))
        self.assertIn(f"Nautobot-Overwrote: {overwritten}", self.tip_fields()["body"])

    def test_non_fast_forward_resets_and_keeps_the_other_file(self):
        saved, revision = self.save_and_push(self.definition())
        calls = {"n": 0}
        real = WorkflowGitRepo.push_once

        def wrapped(repo, branch):
            calls["n"] += 1
            if calls["n"] == 1:
                self.remote_commit({"README.md": "from the other clone\n"}, "side commit")
            return real(repo, branch)

        definition = dict(revision.definition)
        definition["name"] = "After retry"
        with patch.object(WorkflowGitRepo, "push_once", wrapped):
            self.save_and_push(definition, workflow=saved, base_revision=revision.number, summary="Retry")
        saved.refresh_from_db()
        self.assertGreaterEqual(calls["n"], 2)
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC, saved.git_error)
        self.assertEqual(saved.name, "After retry")
        self.assertIn("name: After retry", self.show_file(saved.git_path))
        self.assertIn("from the other clone", self.show_file("README.md"))

    def test_existing_untracked_file_is_not_overwritten(self):
        self.remote_commit({"workflows/taken.yaml": "key: taken\nname: Already there\n"}, "seed")
        original = self.show_file("workflows/taken.yaml")
        saved, _revision = self.save_and_push(self.definition(key="taken", name="From Nautobot"))
        self.assertEqual(saved.git_status, GIT_STATUS_NOT_SYNCED)
        self.assertIn("E-NWE-202", saved.git_error)
        self.assertEqual(self.show_file("workflows/taken.yaml"), original)
        self.assertEqual(saved.name, "From Nautobot")

    def test_auth_failure_is_sanitized_and_keeps_the_save(self):
        message = f"https://user:{TOKEN_VALUE}@example.invalid/wf.git authentication failed"
        with patch.object(WorkflowGitRepo, "push_once", side_effect=GitCommandError(["git", "push"], message)):
            saved, _revision = self.save_and_push(self.definition(key="secret-flow", name="Secret flow"))
        self.assertEqual(saved.name, "Secret flow")
        self.assertEqual(saved.git_status, GIT_STATUS_NOT_SYNCED)
        self.assertNotIn(TOKEN_VALUE, saved.git_error)
        self.assertNotIn("user:", saved.git_error)
        self.assertIn("E-NWE-203", saved.git_error)
        self.assertTrue(Workflow.objects.filter(key="secret-flow").exists())

    def test_secret_error_is_recorded_and_reraised(self):
        with self.captureOnCommitCallbacks(execute=True):
            saved, _revision = publish_workflow(
                user=self.user,
                workflow=None,
                definition=self.definition(key="secret-err"),
                base_revision=None,
                enable=False,
                repository=self.repo,
            )
        saved.git_status = GIT_STATUS_PENDING
        saved.save(update_fields=["git_status", "last_updated"])
        self.url_patch.stop()

        def boom(_record):
            raise SecretError(_record, "environment-variable", f"cannot read {TOKEN_VALUE}")

        with patch(
            "nautobot.extras.datasources.git.get_repo_from_url_to_path_and_from_branch",
            side_effect=boom,
        ):
            with self.assertRaises(SecretError):
                publish_repository(self.repo.pk, self.user)
        self.url_patch.start()
        saved.refresh_from_db()
        self.assertEqual(saved.git_status, GIT_STATUS_NOT_SYNCED)
        self.assertNotIn(TOKEN_VALUE, saved.git_error)
        self.assertIn("E-NWE-203", saved.git_error)

    def test_retry_backoff_and_maintenance_push(self):
        first = next_retry_at(1)
        self.assertAlmostEqual((first - timezone.now()).total_seconds(), 60, delta=15)
        later = next_retry_at(5)
        self.assertAlmostEqual((later - timezone.now()).total_seconds(), 60 * 60, delta=15)
        saved, _revision = self.save_and_push(self.definition(key="retry-flow", name="Retry me"))
        saved.git_status = GIT_STATUS_NOT_SYNCED
        saved.git_next_retry_at = timezone.now() - timedelta(minutes=1)
        saved.git_error = "E-NWE-203 earlier"
        saved.name = "Retry me again"
        definition = dict(saved.published_revision.definition)
        definition["name"] = "Retry me again"
        saved.published_revision.definition = definition
        saved.published_revision.published_at = timezone.now()
        saved.published_revision.save(update_fields=["definition", "published_at"])
        saved.save(update_fields=["git_status", "git_next_retry_at", "git_error", "name", "last_updated"])
        self.repo.current_head = self.head()
        self.repo.save(update_fields=["current_head"])
        cache.delete(f"nwe:git:queued:{self.repo.pk}")
        with patch("nautobot_workflow_engine.utilities.git_publish.enqueue_publish") as enqueued:
            run_git_maintenance()
        enqueued.assert_called()
        publish_repository(self.repo.pk, self.user)
        saved.refresh_from_db()
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC, saved.git_error)
        self.assertIn("name: Retry me again", self.show_file(saved.git_path))

    def test_maintenance_enqueues_sync_when_the_remote_moved(self):
        self.save_and_push(self.definition(key="watch-flow"))
        with patch(
            "nautobot_workflow_engine.utilities.git_publish.enqueue_repository_sync",
            return_value=SimpleNamespace(pk="job"),
        ) as enqueued:
            with patch(
                "nautobot_workflow_engine.utilities.git_probe.GitRemoteProbe.remote_head",
                return_value="a" * 40,
            ):
                run_git_maintenance()
        enqueued.assert_called()

    def test_lock_contention_reports_205(self):
        saved, _revision = self.save_and_push(self.definition(key="locked-flow"))
        saved.git_status = GIT_STATUS_PENDING
        saved.save(update_fields=["git_status", "last_updated"])
        cache.set(lock_key(self.repo.pk), "1", timeout=60)
        try:
            pushed = publish_repository(self.repo.pk, self.user, lock_timeout=0)
        finally:
            cache.delete(lock_key(self.repo.pk))
        self.assertFalse(pushed)
        saved.refresh_from_db()
        self.assertEqual(saved.git_status, GIT_STATUS_NOT_SYNCED)
        self.assertIn("E-NWE-205", saved.git_error)

    def test_two_sequential_publishes_make_two_commits(self):
        self.save_and_push(self.definition(key="one-flow", name="One"))
        self.save_and_push(self.definition(key="two-flow", name="Two"))
        self.assertEqual(self.commit_count(), 2)
        self.assertIn("key: one-flow", self.show_file("workflows/one-flow.yaml"))
        self.assertIn("key: two-flow", self.show_file("workflows/two-flow.yaml"))

    def test_include_adopt_rewrites_canonical_form_once(self):
        fragment = "category: lab\n"
        document = (
            "key: adopted-flow\n"
            "name: Adopted\n"
            "{% include 'extra.yaml' %}\n"
            "actions:\n"
            "  - type: awx\n"
            "    name: run\n"
            "    template_id: 3\n"
        )
        self.remote_commit(
            {"workflows/adopted-flow.yaml": document, "workflows/extra.yaml": fragment},
            "seed include",
        )
        self.pull_into_database()
        saved = Workflow.objects.get(key="adopted-flow")
        self.assertEqual(saved.managed_by, "git")
        warning = adopt_workflow(user=self.user, workflow=saved, confirm=False)
        self.assertFalse(warning["adopted"])
        self.assertIn("extra.yaml", warning["includes"])
        saved.refresh_from_db()
        self.assertEqual(saved.managed_by, "git")
        with self.captureOnCommitCallbacks(execute=True):
            adopted = adopt_workflow(user=self.user, workflow=saved, confirm=True)
        self.assertTrue(adopted["adopted"])
        saved.refresh_from_db()
        self.assertEqual(saved.managed_by, "nautobot")
        if saved.git_status != GIT_STATUS_IN_SYNC:
            saved.git_status = GIT_STATUS_PENDING
            saved.save(update_fields=["git_status", "last_updated"])
            publish_repository(self.repo.pk, self.user)
            saved.refresh_from_db()
        self.assertEqual(saved.git_status, GIT_STATUS_IN_SYNC, saved.git_error)
        rewritten = self.show_file(saved.git_path)
        self.assertNotIn("{% include", rewritten)
        self.assertIn("# Managed by Nautobot Workflow Engine:", rewritten)
        self.assertIn("adopt", self.tip_fields()["subject"])
        before = self.commit_count()
        publish_repository(self.repo.pk, self.user)
        self.assertEqual(self.commit_count(), before)


class GitRaceTestCase(BareRepoMixin, TransactionTestCase):
    """Two overlapping publishes both land. The lock keeps the pushes serial."""

    def test_racing_publishes_both_end_in_sync(self):
        with patch(
            "nautobot_workflow_engine.utilities.git_publish.enqueue_publish",
            side_effect=RuntimeError("deferred"),
        ):
            first, _one = publish_workflow(
                user=self.user,
                workflow=None,
                definition=self.definition(key="race-one", name="Race one"),
                base_revision=None,
                enable=False,
                repository=self.repo,
            )
            second, _two = publish_workflow(
                user=self.user,
                workflow=None,
                definition=self.definition(key="race-two", name="Race two"),
                base_revision=None,
                enable=False,
                repository=self.repo,
            )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.git_status, GIT_STATUS_NOT_SYNCED)
        errors = []

        def _push():
            from django.db import connections

            try:
                publish_repository(self.repo.pk, self.user)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        workers = [threading.Thread(target=_push) for _index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
        self.assertFalse(errors, errors)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.git_status, GIT_STATUS_IN_SYNC, first.git_error)
        self.assertEqual(second.git_status, GIT_STATUS_IN_SYNC, second.git_error)
        self.assertIn("key: race-one", self.show_file("workflows/race-one.yaml"))
        self.assertIn("key: race-two", self.show_file("workflows/race-two.yaml"))
        self.assertGreaterEqual(self.commit_count(), 1)
