"""REST API for change freezes: CRUD, status and an unsaved preview."""

import datetime as dt
from types import SimpleNamespace

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from drf_spectacular.utils import OpenApiParameter, extend_schema
from nautobot.apps.api import ModelViewSet, ValidatedModelSerializer
from nautobot.extras.models import Tag
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from nautobot_workflow_engine import freezes as freeze_rules
from nautobot_workflow_engine.filters import ChangeFreezeFilterSet
from nautobot_workflow_engine.models import ChangeFreeze, Workflow


class ChangeFreezeSerializer(ValidatedModelSerializer):
    """A change freeze. Times are ISO 8601; ``recur_start_time`` is local to ``timezone``."""

    url = serializers.HyperlinkedIdentityField(view_name="plugins-api:nautobot_workflow_engine-api:changefreeze-detail")
    state = serializers.SerializerMethodField()
    schedule = serializers.SerializerMethodField()
    match_tags = serializers.PrimaryKeyRelatedField(queryset=Tag.objects.all(), many=True, required=False)
    workflows = serializers.SlugRelatedField(
        slug_field="key", queryset=Workflow.objects.all(), many=True, required=False
    )
    excluded_workflows = serializers.SlugRelatedField(
        slug_field="key", queryset=Workflow.objects.all(), many=True, required=False
    )
    categories = serializers.ListField(child=serializers.CharField(max_length=100), required=False)

    class Meta:
        """Serializer options."""

        model = ChangeFreeze
        fields = [
            "id",
            "url",
            "display",
            "name",
            "message",
            "enabled",
            "timezone",
            "kind",
            "starts_at",
            "ends_at",
            "recur_start_date",
            "recur_start_time",
            "rrule",
            "duration_minutes",
            "recur_until",
            "warning_lead_minutes",
            "applies_to_all",
            "categories",
            "match_tags",
            "workflows",
            "excluded_workflows",
            "created_by",
            "state",
            "schedule",
            "created",
            "last_updated",
        ]
        read_only_fields = ["created_by", "state", "schedule"]

    def get_state(self, obj):
        """Active, upcoming, expired or disabled."""
        return freeze_rules.freeze_state(obj)

    def get_schedule(self, obj):
        """Plain-language schedule."""
        return freeze_rules.describe_schedule(obj)

    def validate(self, attrs):
        """Model validation, plus: a freeze must cover something."""
        attrs = super().validate(attrs)

        def current(name, default=None):
            if name in attrs:
                return attrs[name]
            if self.instance is not None:
                value = getattr(self.instance, name)
                return list(value.all()) if hasattr(value, "all") else value
            return default

        if not (current("applies_to_all") or current("categories") or current("match_tags") or current("workflows")):
            raise serializers.ValidationError(
                "Choose what the freeze covers: applies_to_all, or at least one category, tag or workflow."
            )
        return attrs

    def create(self, validated_data):
        """Record who created it."""
        request = self.context.get("request")
        if request is not None and getattr(request.user, "is_authenticated", False):
            validated_data.setdefault("created_by", request.user)
        return super().create(validated_data)


class ChangeFreezeViewSet(ModelViewSet):
    """List, create, update and delete change freezes (permissions on Change freeze)."""

    queryset = ChangeFreeze.objects.prefetch_related("match_tags", "workflows", "excluded_workflows")
    serializer_class = ChangeFreezeSerializer
    filterset_class = ChangeFreezeFilterSet


def _window_row(window, freeze):
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "start_label": freeze_rules.format_short(window.start, freeze),
        "end_label": freeze_rules.format_short(window.end, freeze),
    }


def _visible_workflows(user):
    return Workflow.objects.filter(archived=False).restrict(user, "view").prefetch_related("tags")


class ChangeFreezeStatusView(APIView):
    """Is a workflow (or anything the caller can see) frozen at a given time?"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "workflow", str, description="Workflow key. Leave out to ask about every workflow you can see."
            ),
            OpenApiParameter("at", str, description="ISO 8601 time. Default now."),
        ],
        responses={200: dict},
    )
    def get(self, request):
        """Return ``{frozen, active: [...], next}``."""
        at = timezone.now()
        if request.query_params.get("at"):
            parsed = parse_datetime(request.query_params["at"])
            if parsed is None:
                return Response({"detail": "at must be an ISO 8601 date and time."}, status=400)
            at = parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed, dt.timezone.utc)
        key = request.query_params.get("workflow")
        visible = _visible_workflows(request.user)
        if key:
            workflow = visible.filter(key=key).first()
            if workflow is None:
                raise NotFound("Workflow not found.")
            workflows = [workflow]
        else:
            workflows = list(visible)
        freezes = freeze_rules.enabled_freezes()
        active, upcoming = [], []
        for freeze in freezes:
            if not any(freeze_rules.in_scope(freeze, workflow) for workflow in workflows):
                continue
            window = freeze_rules.window_at(freeze, at)
            if window is not None:
                active.append((freeze, window))
            for nxt in freeze_rules.occurrences(freeze, at, at + dt.timedelta(days=freeze_rules.MAX_LOOKAHEAD_DAYS)):
                if nxt.start > at:
                    upcoming.append((freeze, nxt))
                    break
        upcoming.sort(key=lambda item: item[1].start)
        return Response(
            {
                "frozen": bool(active),
                "at": at.isoformat(),
                "active": [
                    {
                        "id": str(freeze.pk),
                        "name": freeze.name,
                        "message": freeze.message,
                        "starts_at": window.start.isoformat(),
                        "ends_at": window.end.isoformat(),
                    }
                    for freeze, window in active
                ],
                "next": (
                    {
                        "id": str(upcoming[0][0].pk),
                        "name": upcoming[0][0].name,
                        "message": upcoming[0][0].message,
                        "starts_at": upcoming[0][1].start.isoformat(),
                        "ends_at": upcoming[0][1].end.isoformat(),
                    }
                    if upcoming
                    else None
                ),
            }
        )


def _local_or_aware(value, zone_name):
    if value in (None, ""):
        return None
    parsed = parse_datetime(str(value).replace("T", " ") if "T" in str(value) else str(value))
    if parsed is None:
        return "invalid"
    if timezone.is_aware(parsed):
        return parsed
    return freeze_rules.localize(parsed, freeze_rules.zone_for(zone_name))


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return "invalid"


def _list(data, name):
    value = data.get(name)
    if value is None and hasattr(data, "getlist"):
        value = data.getlist(name)
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


class ChangeFreezePreviewView(APIView):
    """Preview an unsaved schedule and scope. Nothing is saved."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=dict, responses={200: dict})
    def post(self, request):
        """Return the next windows, the affected count and the scheduled runs inside them."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_changefreeze")
            or user.has_perm("nautobot_workflow_engine.change_changefreeze")
        ):
            raise PermissionDenied("You need permission to add or change change freezes.")
        data = request.data
        zone_name = (data.get("timezone") or "UTC").strip()
        errors = {}
        kind = data.get("kind") or "one_time"
        draft = SimpleNamespace(
            name=data.get("name") or "Preview",
            message=data.get("message") or "",
            enabled=True,
            timezone=zone_name,
            kind=kind,
            starts_at=None,
            ends_at=None,
            recur_start_date=None,
            recur_start_time=None,
            rrule=data.get("rrule") or "",
            duration_minutes=None,
            recur_until=None,
            warning_lead_minutes=120,
            applies_to_all=str(data.get("applies_to_all")).lower() in ("true", "1", "on"),
            categories=[item for item in _list(data, "categories") if item],
            pk=None,
        )
        if kind == "one_time":
            for name in ("starts_at", "ends_at"):
                value = _local_or_aware(data.get(name), zone_name)
                if value == "invalid":
                    errors[name] = "Enter a date and time."
                else:
                    setattr(draft, name, value)
        else:
            draft.recur_start_date = (
                parse_date(str(data.get("recur_start_date") or "")) if data.get("recur_start_date") else None
            )
            draft.recur_start_time = (
                parse_time(str(data.get("recur_start_time") or "")) if data.get("recur_start_time") else None
            )
            draft.recur_until = parse_date(str(data.get("recur_until") or "")) if data.get("recur_until") else None
            minutes = _int_or_none(data.get("duration_minutes"))
            if minutes == "invalid":
                errors["duration_minutes"] = "Enter a whole number of minutes."
            else:
                draft.duration_minutes = minutes
        lead = _int_or_none(data.get("warning_lead_minutes"))
        if lead == "invalid":
            errors["warning_lead_minutes"] = "Enter a whole number of minutes."
        elif lead is not None:
            draft.warning_lead_minutes = lead
        errors.update({key: value for key, value in freeze_rules.schedule_errors(draft).items() if key not in errors})

        scope = freeze_rules.scope_from_values(
            applies_to_all=draft.applies_to_all,
            categories=draft.categories,
            tag_ids=set(
                Tag.objects.filter(pk__in=[item for item in _list(data, "match_tags") if item]).values_list(
                    "pk", flat=True
                )
            ),
            workflow_ids=set(
                Workflow.objects.filter(pk__in=[item for item in _list(data, "workflows") if item]).values_list(
                    "pk", flat=True
                )
            ),
            excluded_ids=set(
                Workflow.objects.filter(
                    pk__in=[item for item in _list(data, "excluded_workflows") if item]
                ).values_list("pk", flat=True)
            ),
        )
        affected = freeze_rules.affected_workflows(draft, scope=scope)
        windows, runs = [], []
        if not errors:
            now = timezone.now()
            windows = freeze_rules.next_windows(draft, now, count=5)
            all_windows = freeze_rules.occurrences(draft, now, now + dt.timedelta(days=freeze_rules.MAX_LOOKAHEAD_DAYS))
            runs = freeze_rules.runs_in_windows([workflow.pk for workflow in affected], all_windows)
        has_scope = scope["all"] or scope["categories"] or scope["tag_ids"] or scope["workflow_ids"]
        return Response(
            {
                "errors": errors,
                "summary": freeze_rules.describe_schedule(draft) if not errors else "",
                "windows": [_window_row(window, draft) for window in windows],
                "has_scope": bool(has_scope),
                "affected_count": len(affected),
                "affected": [workflow.name for workflow in affected[:8]],
                "runs_count": len(runs),
                "runs": [
                    {
                        "id": str(run.pk),
                        "workflow": run.workflow.name,
                        "scheduled_for": run.scheduled_for.isoformat() if run.scheduled_for else None,
                        "status": run.status,
                    }
                    for run in runs[:20]
                ],
            }
        )
