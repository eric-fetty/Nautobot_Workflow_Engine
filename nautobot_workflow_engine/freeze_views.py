"""Change Freezes pages: list, add/edit, detail with affected runs."""

from django import forms
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from nautobot.apps.forms import BootstrapMixin, StaticSelect2Multiple
from nautobot.apps.views import (
    ObjectBulkDestroyViewMixin,
    ObjectChangeLogViewMixin,
    ObjectDestroyViewMixin,
    ObjectDetailViewMixin,
    ObjectEditViewMixin,
    ObjectListViewMixin,
)
from rest_framework.decorators import action

from . import freezes as freeze_rules
from .filters import ChangeFreezeFilterSet
from .forms import ChangeFreezeForm
from .models import ChangeFreeze
from .scheduling import cancel_scheduled_run
from .tables import ChangeFreezeTable, freeze_scope_summary, freeze_state_pill


class ChangeFreezeFilterForm(BootstrapMixin, forms.Form):
    """Filter drawer for the freeze list."""

    model = ChangeFreeze
    q = forms.CharField(required=False, label="Search")
    state = forms.MultipleChoiceField(
        required=False, choices=freeze_rules.STATE_CHOICES, widget=StaticSelect2Multiple()
    )
    enabled = forms.NullBooleanField(
        required=False, widget=forms.Select(choices=[("", "Any"), ("true", "Yes"), ("false", "No")])
    )


class ChangeFreezeUIViewSet(  # pylint: disable=abstract-method
    ObjectListViewMixin,
    ObjectDetailViewMixin,
    ObjectEditViewMixin,
    ObjectDestroyViewMixin,
    ObjectBulkDestroyViewMixin,
    ObjectChangeLogViewMixin,
):
    """Workflows → Change Freezes."""

    queryset = ChangeFreeze.objects.prefetch_related("match_tags", "workflows", "excluded_workflows")
    filterset_class = ChangeFreezeFilterSet
    filterset_form_class = ChangeFreezeFilterForm
    table_class = ChangeFreezeTable
    form_class = ChangeFreezeForm
    action_buttons = ("add",)

    def get_serializer_class(self):
        """The REST serializer (Nautobot uses it for the data and export views)."""
        from .api.serializers import ChangeFreezeSerializer

        return ChangeFreezeSerializer

    def form_save(self, form, **kwargs):
        """Save, and record who created the freeze."""
        obj = form.save(commit=False)
        if not obj.present_in_database and obj.created_by_id is None and self.request.user.is_authenticated:
            obj.created_by = self.request.user
        obj.save()
        form.save_m2m()
        return obj

    def extra_message(self, **kwargs):
        """After a save, say how many scheduled runs fall in the freeze."""
        obj = kwargs.get("obj")
        if obj is None:
            return ""
        runs = freeze_rules.scheduled_runs_in(obj) if obj.enabled else []
        if not runs:
            return ""
        return (
            f". {len(runs)} scheduled run{'s' if len(runs) != 1 else ''} fall in this window and will be "
            "Blocked by freeze when they come due. Review them below"
        )

    def extra_message_context(self, obj):
        """Pass the saved freeze to extra_message."""
        return {"obj": obj}

    def get_return_url(self, request, obj=None, default_return_url=None):
        """After add or edit, open the freeze so the affected runs prompt is visible."""
        if obj is not None and getattr(obj, "present_in_database", False) and self.action in ("create", "update"):
            return obj.get_absolute_url()
        return super().get_return_url(request, obj=obj, default_return_url=default_return_url)

    def get_extra_context(self, request, instance=None):
        """Detail page facts, and the presets and preview URL for the form."""
        context = super().get_extra_context(request, instance)
        if self.action in ("create", "update"):
            context["freeze_presets"] = freeze_rules.PRESETS
            context["freeze_preview_url"] = reverse("plugins-api:nautobot_workflow_engine-api:changefreeze-preview")
            return context
        if instance is None or self.action != "retrieve":
            return context
        now = timezone.now()
        affected = freeze_rules.affected_workflows(instance)
        upcoming = freeze_rules.next_windows(instance, now, count=10)
        waiting = freeze_rules.scheduled_runs_in(instance, now) if instance.enabled else []
        context.update(
            {
                "freeze_state_pill": freeze_state_pill(instance, now),
                "freeze_schedule": freeze_rules.describe_schedule(instance),
                "freeze_scope": freeze_scope_summary(instance),
                "freeze_windows": [
                    {
                        "start": freeze_rules.format_short(window.start, instance),
                        "end": freeze_rules.format_short(window.end, instance),
                        "active": window.contains(now),
                    }
                    for window in upcoming
                ],
                "freeze_affected": affected,
                "freeze_waiting_runs": waiting,
                "freeze_blocked_runs": list(
                    instance.runs.filter(status="blocked")
                    .select_related("workflow", "requested_by")
                    .order_by("-started")[:50]
                ),
                "freeze_override_runs": list(
                    instance.runs.exclude(freeze_override_reason="")
                    .select_related("workflow", "requested_by", "freeze_override_by")
                    .order_by("-started")[:50]
                ),
                "freeze_can_cancel": request.user.has_perm("nautobot_workflow_engine.change_changefreeze", instance),
            }
        )
        return context

    @action(
        detail=True, methods=["post"], url_path="cancel-runs", url_name="cancel_runs", custom_view_base_action="change"
    )
    def cancel_runs(self, request, pk=None):
        """Cancel the Scheduled and Pending Approval runs that fall inside this freeze."""
        freeze = get_object_or_404(ChangeFreeze.objects.restrict(request.user, "change"), pk=pk)
        canceled = 0
        with transaction.atomic():
            for run in freeze_rules.scheduled_runs_in(freeze):
                cancel_scheduled_run(
                    run,
                    f"Canceled by {request.user.username} because it falls in change freeze \u201c{freeze.name}\u201d.",
                )
                canceled += 1
        if canceled:
            messages.success(request, f"Canceled {canceled} scheduled run{'s' if canceled != 1 else ''}.")
        else:
            messages.info(request, "No scheduled runs fall in this freeze.")
        return redirect(freeze.get_absolute_url())
