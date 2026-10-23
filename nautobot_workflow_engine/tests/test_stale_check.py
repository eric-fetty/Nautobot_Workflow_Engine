"""Stale-edit checks against a real remote."""

from unittest.mock import patch

from django.test import TestCase

from nautobot_workflow_engine.services import ServiceError
from nautobot_workflow_engine.services import publish as publish_workflow
from nautobot_workflow_engine.tests.git_support import BareRepoMixin
from nautobot_workflow_engine.utilities.git_probe import GitProbeError


class StaleGitTestCase(BareRepoMixin, TestCase):
    """A save is blocked only when this workflow file changed in Git."""

    def test_stale_revision_writes_nothing(self):
        saved, first = self.save_and_push(self.definition())
        saved, second = self.save_and_push(
            self.definition(name="Second"),
            workflow=saved,
            base_revision=first.number,
            summary="Second",
        )
        before = self.commit_count()
        with self.assertRaises(ServiceError) as caught:
            publish_workflow(
                user=self.user,
                workflow=saved,
                definition=self.definition(name="Third"),
                base_revision=first.number,
                enable=False,
                repository=self.repo,
                base_git_commit=saved.git_commit,
                base_content_hash=saved.git_content_hash,
            )
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.payload["code"], "stale_revision")
        self.assertIn("Updated in Nautobot by", caught.exception.payload["detail"])
        self.assertIn("reload to continue", caught.exception.payload["detail"])
        saved.refresh_from_db()
        self.assertEqual(saved.name, "Second")
        self.assertEqual(saved.published_revision.number, second.number)
        self.assertEqual(self.commit_count(), before)

    def test_stale_git_when_this_file_changed(self):
        saved, revision = self.save_and_push(self.definition())
        text = self.show_file(saved.git_path).replace("name: Lab flow", "name: From Git")
        self.remote_commit({saved.git_path: text}, "edit the workflow", author="Git Editor")
        before = saved.revisions.count()
        with patch("nautobot_workflow_engine.utilities.git_publish.enqueue_repository_sync", return_value=None):
            with self.assertRaises(ServiceError) as caught:
                publish_workflow(
                    user=self.user,
                    workflow=saved,
                    definition=self.definition(name="Mine"),
                    base_revision=revision.number,
                    enable=False,
                    repository=self.repo,
                    base_git_commit=saved.git_commit,
                    base_content_hash=saved.git_content_hash,
                )
        self.assertEqual(caught.exception.payload["code"], "stale_git")
        self.assertTrue(
            caught.exception.payload["detail"].startswith("Updated in Git by Git Editor at "),
            caught.exception.payload["detail"],
        )
        self.assertIn("reload to continue", caught.exception.payload["detail"])
        saved.refresh_from_db()
        self.assertEqual(saved.name, "Lab flow")
        self.assertEqual(saved.revisions.count(), before)

    def test_other_files_do_not_block_the_save(self):
        saved, revision = self.save_and_push(self.definition())
        self.remote_commit({"README.md": "unrelated\n"}, "readme only", author="Git Editor")
        updated, _newer = self.save_and_push(
            self.definition(name="Still mine"),
            workflow=saved,
            base_revision=revision.number,
            summary="Other file",
        )
        self.assertEqual(updated.name, "Still mine")
        self.assertIn("name: Still mine", self.show_file(updated.git_path))

    def test_unreachable_git_does_not_block_the_save(self):
        saved, revision = self.save_and_push(self.definition())
        with patch(
            "nautobot_workflow_engine.utilities.git_probe.GitRemoteProbe.remote_head",
            side_effect=GitProbeError("no route to host"),
        ):
            updated, _newer = publish_workflow(
                user=self.user,
                workflow=saved,
                definition=self.definition(name="Unchecked"),
                base_revision=revision.number,
                enable=False,
                repository=self.repo,
                base_git_commit=saved.git_commit,
                base_content_hash=saved.git_content_hash,
                commit_summary="Unchecked",
            )
        self.assertEqual(updated.name, "Unchecked")
        self.assertIn("git_unverified", updated._git_warnings)
