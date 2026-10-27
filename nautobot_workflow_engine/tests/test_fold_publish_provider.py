"""The old "Workflow publishing" provider is folded into "workflows"."""

import importlib
import uuid

from django.apps import apps as django_apps
from django.test import TestCase
from nautobot.extras.models import GitRepository
from nautobot.extras.registry import registry

from nautobot_workflow_engine.models import LEGACY_PUBLISH_CONTENT, WORKFLOWS_CONTENT

migration = importlib.import_module("nautobot_workflow_engine.migrations.0012_fold_publish_provider")


def repo(contents):
    return GitRepository.objects.create(
        name=f"Fold {uuid.uuid4().hex[:8]}",
        remote_url=f"https://example.invalid/fold-{uuid.uuid4().hex[:6]}.git",
        branch="main",
        provided_contents=list(contents),
    )


class FoldPublishProviderTests(TestCase):
    def test_only_the_workflows_provider_is_registered(self):
        identifiers = [entry.content_identifier for entry in registry["datasource_contents"]["extras.gitrepository"]]
        self.assertIn(WORKFLOWS_CONTENT, identifiers)
        self.assertNotIn(LEGACY_PUBLISH_CONTENT, identifiers)

    def test_migration_drops_the_old_value_and_keeps_the_rest(self):
        both = repo([WORKFLOWS_CONTENT, LEGACY_PUBLISH_CONTENT, "extras.jobs"])
        legacy_only = repo([LEGACY_PUBLISH_CONTENT])
        untouched = repo([WORKFLOWS_CONTENT])
        migration.drop_legacy_provider(django_apps, None)
        both.refresh_from_db()
        legacy_only.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(both.provided_contents, [WORKFLOWS_CONTENT, "extras.jobs"])
        self.assertEqual(legacy_only.provided_contents, [])
        self.assertEqual(untouched.provided_contents, [WORKFLOWS_CONTENT])

    def test_migration_is_safe_to_run_twice(self):
        both = repo([WORKFLOWS_CONTENT, LEGACY_PUBLISH_CONTENT])
        migration.drop_legacy_provider(django_apps, None)
        migration.drop_legacy_provider(django_apps, None)
        both.refresh_from_db()
        self.assertEqual(both.provided_contents, [WORKFLOWS_CONTENT])
