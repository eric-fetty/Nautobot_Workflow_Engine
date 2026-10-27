"""GitHub and GitLab push webhooks. The secret lives in the repository secrets group."""

import hashlib
import hmac
import json

from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import GitRepository
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from nautobot_workflow_engine.utilities.git import sanitize_git_error
from nautobot_workflow_engine.utilities.git_publish import enqueue_repository_sync


def _webhook_secret(repository):
    group = repository.secrets_group
    if group is None:
        return None
    try:
        value = group.get_secret_value(
            SecretsGroupAccessTypeChoices.TYPE_GENERIC,
            SecretsGroupSecretTypeChoices.TYPE_KEY,
            obj=repository,
        )
    except Exception:
        return None
    return value or None


def _signature_ok(repository, request, secret):
    github = request.headers.get("X-Hub-Signature-256", "")
    gitlab = request.headers.get("X-Gitlab-Token", "")
    if github:
        digest = hmac.new(str(secret).encode("utf-8"), request.body or b"", hashlib.sha256).hexdigest()
        return hmac.compare_digest(github, f"sha256={digest}")
    if gitlab:
        return hmac.compare_digest(str(gitlab), str(secret))
    return False


def _event_branch(request, payload):
    github_event = request.headers.get("X-GitHub-Event", "")
    if github_event and github_event != "push":
        return None
    if payload.get("object_kind") and payload.get("object_kind") != "push" and not github_event:
        return None
    ref = payload.get("ref") or ""
    if ref.startswith("refs/heads/"):
        return ref.split("/", 2)[-1]
    return None


class WebhookResponseSerializer(serializers.Serializer):
    """Webhook acknowledgement."""

    enqueued = serializers.BooleanField(required=False)
    ignored = serializers.BooleanField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True)


@method_decorator(csrf_exempt, name="dispatch")
class GitWebhookView(APIView):
    """Accept a signed push event and enqueue one repository sync."""

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = WebhookResponseSerializer

    @extend_schema(request=None, responses={202: WebhookResponseSerializer, 403: None, 404: None})
    def post(self, request, repository_id):
        """Verify the signature and enqueue a sync for pushes to the configured branch."""
        try:
            repository = GitRepository.objects.get(pk=repository_id)
        except (GitRepository.DoesNotExist, ValueError, TypeError):
            return Response(status=404)
        secret = _webhook_secret(repository)
        if not secret:
            return Response(status=404)
        if not _signature_ok(repository, request, secret):
            return Response(status=403)
        try:
            payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        branch = _event_branch(request, payload)
        if branch is None or branch != (repository.branch or "main"):
            return Response({"ignored": True}, status=202)
        marker = f"nwe:webhook:{repository.pk}"
        if not cache.add(marker, "1", timeout=10):
            return Response({"enqueued": False, "reason": "duplicate"}, status=202)
        try:
            enqueue_repository_sync(repository, None)
        except Exception as exc:
            return Response({"enqueued": False, "reason": sanitize_git_error(exc)}, status=202)
        return Response({"enqueued": True}, status=202)
