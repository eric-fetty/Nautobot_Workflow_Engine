"""Serializers for the builder API. They document the OpenAPI schema."""

from rest_framework import serializers


class FieldErrorSerializer(serializers.Serializer):
    """One JSON Pointer error."""

    pointer = serializers.CharField(required=False, allow_blank=True)
    code = serializers.CharField()
    message = serializers.CharField()
    section = serializers.CharField(required=False, allow_blank=True)
    item = serializers.CharField(required=False, allow_blank=True)
    field_label = serializers.CharField(required=False, allow_blank=True)
    path = serializers.CharField(required=False, allow_blank=True)


class ErrorListSerializer(serializers.Serializer):
    """400 body."""

    errors = FieldErrorSerializer(many=True)


class DetailSerializer(serializers.Serializer):
    """403 or 404 body."""

    detail = serializers.CharField()


class StaleSerializer(serializers.Serializer):
    """409 body for a stale revision or, in M3, a stale Git file."""

    code = serializers.CharField()
    detail = serializers.CharField()
    author = serializers.CharField(required=False, allow_blank=True)
    time = serializers.CharField(required=False, allow_blank=True)
    commit = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    current_revision = serializers.IntegerField(required=False, allow_null=True)


class DefinitionRequestSerializer(serializers.Serializer):
    """A canonical definition, YAML text, or the current builder payload."""

    definition = serializers.JSONField(required=False)
    yaml = serializers.CharField(required=False, allow_blank=True)
    payload = serializers.JSONField(required=False)
    repository = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True)
    base_revision = serializers.IntegerField(required=False, allow_null=True)
    base_git_commit = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    base_content_hash = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    enable = serializers.BooleanField(required=False, allow_null=True)
    commit_summary = serializers.CharField(required=False, allow_blank=True)
    approval_confirm = serializers.BooleanField(required=False)


class ValidateResponseSerializer(serializers.Serializer):
    """Preview produced by normalize and validate. Never a 500."""

    definition = serializers.JSONField(required=False, allow_null=True)
    yaml = serializers.CharField(required=False, allow_blank=True)
    errors = FieldErrorSerializer(many=True)
    warnings = serializers.ListField(child=serializers.CharField(), required=False)
    references = serializers.JSONField(required=False)


class RefactorRequestSerializer(serializers.Serializer):
    """Reference-safe rename."""

    definition = serializers.JSONField()
    op = serializers.ChoiceField(choices=["rename_input", "rename_action"])
    from_name = serializers.CharField(source="from")
    to = serializers.CharField()


class RefactorResponseSerializer(serializers.Serializer):
    """Renamed definition and the pointers that changed."""

    definition = serializers.JSONField()
    changes = serializers.ListField(child=serializers.JSONField())


class RevisionSerializer(serializers.Serializer):
    """One workflow revision."""

    number = serializers.IntegerField()
    state = serializers.CharField()
    source = serializers.CharField()
    definition = serializers.JSONField(required=False)
    yaml = serializers.CharField(required=False, allow_blank=True)
    content_hash = serializers.CharField(required=False, allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True)
    created = serializers.DateTimeField(required=False, allow_null=True)
    created_by = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    published_at = serializers.DateTimeField(required=False, allow_null=True)
    published_by = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    git_commit = serializers.CharField(required=False, allow_blank=True)
    git_author = serializers.CharField(required=False, allow_blank=True)
    base_revision = serializers.IntegerField(required=False, allow_null=True)
    outdated = serializers.BooleanField(required=False)
    superseded_reason = serializers.CharField(required=False, allow_blank=True)


class WorkflowSummarySerializer(serializers.Serializer):
    """List row."""

    key = serializers.CharField()
    name = serializers.CharField()
    category = serializers.CharField(allow_blank=True)
    managed_by = serializers.CharField()
    enabled = serializers.BooleanField()
    archived = serializers.BooleanField()
    git_status = serializers.CharField()
    published_revision = serializers.IntegerField(allow_null=True)
    draft_revision = serializers.IntegerField(allow_null=True)
    updated = serializers.DateTimeField(allow_null=True)


class BaseSerializer(serializers.Serializer):
    """The editor records this and sends it back on save. Git fields are empty until M3."""

    revision = serializers.IntegerField(allow_null=True)
    git_commit = serializers.CharField(allow_blank=True)
    git_content_hash = serializers.CharField(allow_blank=True)


class WorkflowDetailSerializer(serializers.Serializer):
    """Workflow plus published and draft revisions."""

    workflow = WorkflowSummarySerializer()
    published = RevisionSerializer(allow_null=True)
    draft = RevisionSerializer(allow_null=True)
    base = BaseSerializer()


class DiffSerializer(serializers.Serializer):
    """YAML for Nautobot's diff view."""

    original_yaml = serializers.CharField(allow_blank=True)
    modified_yaml = serializers.CharField(allow_blank=True)


class DuplicateSerializer(serializers.Serializer):
    """New key and name for a copy."""

    key = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
