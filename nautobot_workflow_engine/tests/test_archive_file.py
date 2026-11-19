"""Archiving removes the workflow's file unless someone changed it in Git."""

import tempfile
import unittest
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from nautobot_workflow_engine.utilities.git_publish import _archive_file

PUBLISHED_AT = datetime(2026, 11, 19, 12, 0, 0, 900000, tzinfo=dt_timezone.utc)
# Git keeps whole seconds, so our own push a moment later reads as 12:00:01.
OUR_PUSH_AT = PUBLISHED_AT.replace(microsecond=0) + timedelta(seconds=1)


class ArchiveFileTestCase(unittest.TestCase):
    """The newer-change check must not mistake our own earlier push for someone else's edit."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "lab-flow.yaml"
        self.path.write_text("key: lab-flow\n")
        # A later commit that didn't touch the file moved git_commit past the file's last commit.
        self.workflow = SimpleNamespace(key="lab-flow", git_commit="readme-commit", git_path="workflows/lab-flow.yaml")
        self.revision = SimpleNamespace(published_at=PUBLISHED_AT, comment="", published_by=None, created_by=None)
        self.touch = {"sha": "our-publish-commit", "at": OUR_PUSH_AT}

    def tearDown(self):
        self.folder.cleanup()

    def _archive(self, unchanged):
        return _archive_file(
            self.workflow, self.path, True, True, self.touch, self.revision, MagicMock(), unchanged=unchanged
        )

    def test_unchanged_file_is_removed_even_when_git_time_reads_later(self):
        entry = self._archive(unchanged=True)
        self.assertIsNotNone(entry)
        self.assertFalse(self.path.exists())

    def test_file_changed_in_git_after_publishing_is_kept(self):
        entry = self._archive(unchanged=False)
        self.assertIsNone(entry)
        self.assertTrue(self.path.exists())
