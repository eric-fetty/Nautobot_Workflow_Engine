"""One bad workflow file does not abort the rest of a Git sync."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from django.test import TestCase
from nautobot.extras.models import GitRepository

from nautobot_workflow_engine import datasources
from nautobot_workflow_engine.models import Workflow


class SyncPipelineTestCase(TestCase):
    """Field errors are logged and the next file is still imported."""

    def test_invalid_file_does_not_abort_the_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "workflows"
            folder.mkdir()
            (folder / "bad.yaml").write_text(
                "key: bad-flow\nname: Bad\nactions:\n  - type: nautobot\n    name: missing\n    script: scripts/missing.py\n",
                encoding="utf-8",
            )
            (folder / "boom.yaml").write_text(
                "key: boom-flow\nname: Boom\ninputs:\n  - key: device\n    type: object\n    model: dcim.NotARealModel\nactions:\n  - type: awx\n    name: run\n    template_id: 2\n",
                encoding="utf-8",
            )
            (folder / "good.yaml").write_text(
                "key: good-flow\nname: Good\nactions:\n  - type: awx\n    name: run\n    template_id: 1\n",
                encoding="utf-8",
            )
            repo = GitRepository.objects.create(name="Pipeline Repo", remote_url="https://example.com/pipeline.git")
            job_result = MagicMock()
            with (
                patch.object(GitRepository, "filesystem_path", new_callable=PropertyMock, return_value=str(root)),
                patch.object(datasources, "_sync_repo_python_modules"),
                patch.object(datasources, "validate_repo_filter_modules"),
                patch.object(datasources, "clear_repo_script_cache"),
            ):
                datasources.sync_workflows_from_repo(repo, job_result, delete=False)
        self.assertTrue(Workflow.objects.filter(key="good-flow", enabled=True).exists())
        self.assertFalse(Workflow.objects.filter(key="bad-flow").exists())
        self.assertFalse(Workflow.objects.filter(key="boom-flow").exists())
        logged = " ".join(
            str(call.args[0] if call.args else call.kwargs.get("message", "")) for call in job_result.log.call_args_list
        )
        self.assertIn("E-NWE-102", logged)
        self.assertIn("/actions/0/script", logged)
