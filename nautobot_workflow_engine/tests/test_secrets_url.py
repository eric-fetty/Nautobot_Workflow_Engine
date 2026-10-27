"""Credential URLs quote the username and token."""

import os
import uuid
from urllib.parse import quote

from django.test import TestCase
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.datasources.git import get_repo_access_url
from nautobot.extras.models import GitRepository, Secret, SecretsGroup, SecretsGroupAssociation


class SecretsUrlTestCase(TestCase):
    """Symbols in the secrets group are percent-encoded before they reach Git."""

    def test_username_and_token_are_quoted(self):
        username = "user name"
        token = "p@ss/word"
        os.environ["NWE_URL_USER"] = username
        os.environ["NWE_URL_TOKEN"] = token
        self.addCleanup(lambda: os.environ.pop("NWE_URL_USER", None))
        self.addCleanup(lambda: os.environ.pop("NWE_URL_TOKEN", None))
        user_secret = Secret.objects.create(
            name=f"user-{uuid.uuid4().hex[:8]}",
            provider="environment-variable",
            parameters={"variable": "NWE_URL_USER"},
        )
        token_secret = Secret.objects.create(
            name=f"token-{uuid.uuid4().hex[:8]}",
            provider="environment-variable",
            parameters={"variable": "NWE_URL_TOKEN"},
        )
        group = SecretsGroup.objects.create(name=f"quoted-{uuid.uuid4().hex[:8]}")
        SecretsGroupAssociation.objects.create(
            secrets_group=group,
            secret=user_secret,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
        )
        SecretsGroupAssociation.objects.create(
            secrets_group=group,
            secret=token_secret,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN,
        )
        repository = GitRepository.objects.create(
            name=f"Quoted {uuid.uuid4().hex[:8]}",
            remote_url="https://example.invalid/quoted.git",
            secrets_group=group,
        )
        url = get_repo_access_url(repository)
        self.assertIn(quote(username, safe=""), url)
        self.assertIn(quote(token, safe=""), url)
        self.assertNotIn(username, url)
        self.assertNotIn(token, url)
