"""Views for the workflow engine plugin."""

import logging
from datetime import timezone as dt_timezone
from math import ceil, floor
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, OuterRef, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from nautobot.apps.views import NautobotUIViewSet, ObjectPermissionRequiredMixin, ObjectView

from .approvals import nautobot_supports_approval_workflows
from .executors import _get_workflow_queryset, run_workflow
from .filters import WorkflowFilterSet
from .forms import DynamicWorkflowForm
from .models import WorkflowInput, WorkflowRun
from .scheduling import create_approval_gated_immediate_schedule
from .servicenow import ServiceNowValidationError, get_change_window
from .tables import WorkflowTable

logger = logging.getLogger(__name__)
PLUGIN_CFG = settings.PLUGINS_CONFIG.get("nautobot_workflow_engine", {})

TERMINAL_WORKFLOW_STATUSES = {"success", "failed", "partial", "canceled"}
STATUS_BADGE_MAP = {
    "success": {"label": "Success", "class": "bg-success", "icon": "mdi-check"},
    "failed": {"label": "Failed", "class": "bg-danger", "icon": "mdi-close"},
    "running": {"label": "Running", "class": "bg-primary", "icon": "mdi-play"},
    "pending": {"label": "Pending", "class": "bg-warning text-dark", "icon": "mdi-clock"},
    "pending_approval": {
        "label": "Pending Approval",
        "class": "bg-warning text-dark",
        "icon": "mdi-account-check",
    },
    "scheduled": {"label": "Scheduled", "class": "bg-info text-dark", "icon": "mdi-calendar-clock"},
    "canceled": {"label": "Canceled", "class": "bg-secondary", "icon": "mdi-cancel"},
    "partial": {"label": "Partial", "class": "bg-warning text-dark", "icon": "mdi-alert"},
}

TERMINAL_AWX_STATUSES = {"successful", "failed", "error", "canceled"}
RUN_HISTORY_PAGE_SIZE_OPTIONS = (10, 25, 50, 100)
RUN_HISTORY_DEFAULT_PAGE_SIZE = 50
RUN_HISTORY_SESSION_KEY = "nautobot_workflow_engine_run_history_filters"
RUN_HISTORY_PERSISTED_KEYS = ("workflow", "status", "user", "category", "time_range", "start", "end", "per_page")


def _format_duration_seconds(total_seconds):
    """Format duration in seconds as compact human-readable text."""
    if total_seconds < 0:
        return "-"
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _estimate_expected_runtime_seconds(workflow_run):
    """Estimate expected runtime for progress tracking using historical durations."""
    workflow = workflow_run.workflow
    if not workflow:
        return 600

    sample_size = 20
    trim_ratio = 0.10

    recent_terminal_runs = (
        WorkflowRun.objects.filter(workflow=workflow, finished__isnull=False)
        .exclude(status="canceled")
        .exclude(started__isnull=True)
        .order_by("-finished")[:sample_size]
    )

    durations = []
    for run in recent_terminal_runs:
        if run.finished and run.started and run.finished >= run.started:
            durations.append(int((run.finished - run.started).total_seconds()))

    if durations:
        durations.sort()
        trim_count = int(len(durations) * trim_ratio)
        if (2 * trim_count) < len(durations):
            durations = durations[trim_count : len(durations) - trim_count]

        midpoint = len(durations) // 2
        if len(durations) % 2 == 1:
            median_seconds = durations[midpoint]
        else:
            median_seconds = int((durations[midpoint - 1] + durations[midpoint]) / 2)

        return max(60, median_seconds)

    # No usable history yet.
    return 600


def _serialize_workflow_run_progress(workflow_run):
    """Serialize a workflow run into a progress payload for polling clients."""
    status_badge = STATUS_BADGE_MAP.get(
        workflow_run.status,
        {"label": workflow_run.status.title(), "class": "bg-secondary", "icon": "mdi-help-circle"},
    )

    if workflow_run.finished and workflow_run.started and workflow_run.finished >= workflow_run.started:
        exec_duration_seconds = int((workflow_run.finished - workflow_run.started).total_seconds())
        execution_duration_display = _format_duration_seconds(exec_duration_seconds)
    elif workflow_run.status == "running":
        execution_duration_display = "Still running..."
        exec_duration_seconds = (
            int((timezone.now() - workflow_run.started).total_seconds()) if workflow_run.started else 0
        )
    else:
        execution_duration_display = "-"
        exec_duration_seconds = None

    awx_runs = []
    awx_completed = 0
    for awx_run in workflow_run.awx_runs.all().order_by("id"):
        if awx_run.status in TERMINAL_AWX_STATUSES:
            awx_completed += 1
        awx_runs.append(
            {
                "template_id": awx_run.template_id,
                "job_id": awx_run.job_id,
                "url": awx_run.url,
                "status": awx_run.status,
            }
        )

    expected_runtime_seconds = _estimate_expected_runtime_seconds(workflow_run)
    elapsed_seconds = (
        max(0, int((timezone.now() - workflow_run.started).total_seconds())) if workflow_run.started else 0
    )
    time_progress = min(95, int((elapsed_seconds / expected_runtime_seconds) * 100)) if expected_runtime_seconds else 0

    progress_percent = 0
    progress_text = status_badge["label"]
    if workflow_run.status in TERMINAL_WORKFLOW_STATUSES:
        progress_percent = 100
        progress_text = f"{status_badge['label']}"
    elif workflow_run.status == "scheduled":
        progress_percent = 5
        progress_text = "Scheduled"
    elif workflow_run.status == "pending_approval":
        progress_percent = 5
        progress_text = "Awaiting approval"
    elif workflow_run.status == "pending":
        progress_percent = max(10, min(25, time_progress))
        progress_text = "Queued"
    elif workflow_run.status == "running":
        total_awx = len(awx_runs)
        step_progress = 40
        if total_awx > 0:
            step_progress = int((awx_completed / total_awx) * 100)
            progress_text = (
                f"Running ({awx_completed}/{total_awx} AWX jobs complete) • "
                f"elapsed {_format_duration_seconds(elapsed_seconds)}"
            )
        else:
            progress_text = f"Running • elapsed {_format_duration_seconds(elapsed_seconds)}"

        # Blend runtime estimate with step completion for smoother, more realistic progress.
        progress_percent = min(95, max(15, int((0.7 * time_progress) + (0.3 * step_progress))))
    else:
        progress_percent = 15

    return {
        "pk": str(workflow_run.pk),
        "status": workflow_run.status,
        "status_label": status_badge["label"],
        "status_badge": status_badge,
        "started": workflow_run.started.isoformat() if workflow_run.started else None,
        "finished": workflow_run.finished.isoformat() if workflow_run.finished else None,
        "is_terminal": workflow_run.status in TERMINAL_WORKFLOW_STATUSES,
        "execution_duration_seconds": exec_duration_seconds,
        "execution_duration_display": execution_duration_display,
        "progress_percent": progress_percent,
        "progress_text": progress_text,
        "has_post_action_errors": bool(
            isinstance(workflow_run.phase_results, dict)
            and workflow_run.phase_results.get("post", {}).get("any_failed", False)
        ),
        "log": workflow_run.log or "",
        "user_log": workflow_run.user_log or "",
        "awx_runs": awx_runs,
    }


class WorkflowUIViewSet(NautobotUIViewSet):
    """UIViewSet for Workflow model to enable search functionality."""

    queryset = _get_workflow_queryset()

    filterset_class = WorkflowFilterSet
    table_class = WorkflowTable


class WorkflowListView(TemplateView, ObjectPermissionRequiredMixin):
    """List all workflows with category filtering and grouping."""

    template_name = "nautobot_workflow_engine/workflow_list.html"
    permission_required = "nautobot_workflow_engine.view_workflow"
    queryset = _get_workflow_queryset().order_by("category", "name")  # Only show enabled workflows

    def get_queryset(self):
        """Get workflows queryset with optional category filtering."""
        qs = _get_workflow_queryset().order_by("category", "name")
        permitted_ids = [wf.id for wf in qs if self.request.user.has_perm("nautobot_workflow_engine.view_workflow", wf)]
        qs = qs.filter(id__in=permitted_ids)
        # Filter by category if specified
        category = self.request.GET.get("category")
        if category:
            qs = qs.filter(category=category)

        latest_workflow_run = WorkflowRun.objects.filter(workflow_id=OuterRef("pk")).order_by("-started", "-id")
        qs = qs.annotate(
            last_run_started=Subquery(latest_workflow_run.values("started")[:1]),
            last_run_requested_by=Subquery(latest_workflow_run.values("requested_by__username")[:1]),
            last_run_status=Subquery(latest_workflow_run.values("status")[:1]),
        )

        return qs

    def get_context_data(self, **kwargs):
        """Add context data including category grouping."""
        context = super().get_context_data(**kwargs)

        workflows = self.get_queryset()
        selected_category = self.request.GET.get("category", "")
        display = self.request.GET.get("display", "list")
        if display not in {"list", "tiles"}:
            display = "list"

        # Check if user can execute workflows
        can_execute = self.request.user.has_perm("nautobot_workflow_engine.add_workflowrun")
        context["can_execute_workflows"] = can_execute
        context["display"] = display
        context["is_tiles_display"] = display == "tiles"

        context["object_list"] = workflows
        context["workflow_list"] = workflows
        context["workflows"] = workflows

        # Group workflows by category
        workflows_by_category = {}
        for workflow in workflows:
            category = workflow.category or "Uncategorized"
            if category not in workflows_by_category:
                workflows_by_category[category] = []
            workflows_by_category[category].append(workflow)

        context["workflows_by_category"] = workflows_by_category
        context["workflow_group_count"] = len(workflows_by_category)

        # Get all available categories for filter dropdown
        all_categories = (
            _get_workflow_queryset()
            .exclude(category="")
            .exclude(category__isnull=True)
            .values_list("category", flat=True)
            .distinct()
            .order_by("category")
        )
        context["categories"] = list(all_categories)
        context["selected_category"] = selected_category

        list_display_params = {}
        tiles_display_params = {"display": "tiles"}
        if selected_category:
            list_display_params["category"] = selected_category
            tiles_display_params["category"] = selected_category
        context["list_display_query"] = urlencode(list_display_params)
        context["tiles_display_query"] = urlencode(tiles_display_params)

        clear_filter_params = {}
        if display == "tiles":
            clear_filter_params["display"] = "tiles"
        context["clear_filter_query"] = urlencode(clear_filter_params)

        all_categories_params = {}
        if display == "tiles":
            all_categories_params["display"] = "tiles"
        context["all_categories_query"] = urlencode(all_categories_params)

        categories_with_query = []
        for category in context["categories"]:
            params = {"category": category}
            if display == "tiles":
                params["display"] = "tiles"
            categories_with_query.append({"name": category, "query": urlencode(params)})
        context["categories_with_query"] = categories_with_query

        return context


class WorkflowRunCreateView(TemplateView, ObjectPermissionRequiredMixin):
    """View for launching workflows with dynamic forms."""

    template_name = "nautobot_workflow_engine/workflow_launch.html"
    permission_required = "nautobot_workflow_engine.add_workflowrun"

    def get_context_data(self, **kwargs):
        """Add workflow and form to context.

        Optionally prefill form fields from a previous WorkflowRun when the
        request includes ?from_run=<pk>. Only prefill when the previous run
        belongs to the same workflow. Scheduling fields are intentionally
        not prefilled.
        """
        context = super().get_context_data(**kwargs)
        qs = _get_workflow_queryset()
        wf = get_object_or_404(qs, key=kwargs["key"])

        initial = {}
        from_run = self.request.GET.get("from_run")
        if from_run:
            try:
                prev_run = WorkflowRun.objects.get(pk=from_run)
                if prev_run.workflow_id == wf.id and prev_run.inputs:
                    # Build initial mapping from workflow inputs to the values
                    # stored in the previous run. Convert shapes where the
                    # form expects specific types (JSON string for list,
                    # object id string for object fields, list of strings for multichoice,
                    # and string for single-choice fields).
                    import json

                    for wi in wf.inputs.all():
                        if wi.key not in prev_run.inputs:
                            continue
                        val = prev_run.inputs.get(wi.key)
                        if val is None:
                            continue

                        if wi.input_type == "list":
                            # List widget stores JSON string
                            initial[wi.key] = val if isinstance(val, str) else json.dumps(val)
                        elif wi.input_type == "listdict":
                            # List-of-dicts widget stores JSON string
                            initial[wi.key] = val if isinstance(val, str) else json.dumps(val)
                        elif wi.input_type == "boolean":
                            initial[wi.key] = bool(val)
                        elif wi.input_type == "object":
                            # Store the object's PK as a string for the field
                            if isinstance(val, dict):
                                initial[wi.key] = str(val.get("id") or val.get("pk") or "")
                            else:
                                initial[wi.key] = str(val)
                        elif wi.input_type == "multichoice":
                            # Ensure multichoice initial is a list of strings
                            if isinstance(val, (list, tuple)):
                                initial[wi.key] = [str(x) for x in val]
                            else:
                                initial[wi.key] = [str(val)]
                        elif wi.input_type == "choice":
                            # Ensure choice initial is a string matching option values
                            initial[wi.key] = "" if val in (None, "") else str(val)
                        else:
                            # Default: cast to string for safety where appropriate
                            initial[wi.key] = val if isinstance(val, (dict, list)) else val
            except WorkflowRun.DoesNotExist:
                # If the run can't be found, just render an empty form
                pass

        form = DynamicWorkflowForm(wf, initial=initial)
        context["workflow"] = wf
        context["form"] = form
        return context

    def post(self, request, key):
        """Handle workflow launch form submission."""
        qs = _get_workflow_queryset()
        wf = get_object_or_404(qs, key=key)
        form = DynamicWorkflowForm(wf, data=request.POST)
        if form.is_valid():
            # Extract scheduling data
            execution_type = form.cleaned_data.pop("execution_type", "immediate")
            scheduled_datetime = form.cleaned_data.pop("scheduled_datetime", None)
            schedule_source = ""
            schedule_external_system = ""
            schedule_change_ticket = ""
            schedule_external_record_id = ""
            schedule_window_start = None
            schedule_window_end = None
            schedule_last_synced = None
            schedule_sync_error = ""

            if execution_type == "servicenow":
                change_input_key = wf.servicenow_change_input_key
                change_ticket = form.cleaned_data.get(change_input_key) if change_input_key else ""
                try:
                    change_window = get_change_window(change_ticket=change_ticket, workflow=wf)
                except ServiceNowValidationError as exc:
                    if change_input_key and change_input_key in form.fields:
                        form.add_error(change_input_key, str(exc))
                    else:
                        form.add_error(None, str(exc))
                    return render(
                        request,
                        "nautobot_workflow_engine/workflow_launch.html",
                        {"workflow": wf, "form": form},
                    )

                execution_type = "scheduled"
                scheduled_datetime = change_window.window_start
                schedule_source = "servicenow"
                schedule_external_system = "ServiceNow"
                schedule_change_ticket = change_window.change_number
                schedule_external_record_id = change_window.sys_id
                schedule_window_start = change_window.window_start
                schedule_window_end = change_window.window_end
                schedule_last_synced = timezone.now()

            elif execution_type == "scheduled":
                schedule_source = "manual"

            # Capture actual form-fill duration from client (in seconds)
            try:
                form_duration_seconds = int(request.POST.get("form_duration_seconds", "0") or "0")
                if form_duration_seconds < 0:
                    form_duration_seconds = 0
            except ValueError:
                form_duration_seconds = 0

            # Create WorkflowRun with appropriate status
            # For scheduled workflows, start in "running" while pre-actions are prepared asynchronously,
            # then transition to "scheduled" once the future ScheduledJob is created.
            status = "running" if execution_type == "scheduled" else "pending"
            run_scheduled_for = scheduled_datetime if execution_type == "scheduled" else None

            run = WorkflowRun.objects.create(
                workflow=wf,
                requested_by=request.user,
                inputs=form.cleaned_data,
                status=status,
                scheduled_for=run_scheduled_for,
                schedule_source=schedule_source,
                schedule_external_system=schedule_external_system,
                schedule_change_ticket=schedule_change_ticket,
                schedule_external_record_id=schedule_external_record_id,
                schedule_window_start=schedule_window_start,
                schedule_window_end=schedule_window_end,
                schedule_last_synced=schedule_last_synced,
                schedule_sync_error=schedule_sync_error,
                form_duration_seconds=form_duration_seconds,
            )

            if execution_type == "immediate":
                if wf.approval_required and nautobot_supports_approval_workflows():
                    # Route through a near-term ScheduledJob so Nautobot's approval pipeline applies.
                    run.scheduled_for = timezone.now()
                    run.status = "pending_approval"
                    run.log = (
                        f"{run.log}\nAwaiting Nautobot approval before execution...".strip()
                        if run.log
                        else "Awaiting Nautobot approval before execution..."
                    )
                    run.save(update_fields=["scheduled_for", "status", "log"])
                    try:
                        create_approval_gated_immediate_schedule(run)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Failed to create approval-gated schedule for run %s", run.pk)
                        run.status = "failed"
                        run.finished = timezone.now()
                        run.log = f"{run.log}\nFailed to submit for approval: {exc}".strip()
                        run.save(update_fields=["status", "finished", "log"])
                        messages.error(
                            request,
                            f"Unable to submit '{wf.name}' for approval: {exc}",
                        )
                        return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=run.pk)

                    messages.info(
                        request,
                        f"'{wf.name}' was submitted for approval. Approve it under Approvals → Approval Dashboard "
                        "before it will run. Ensure the synced approval definition has at least one stage configured.",
                    )
                    return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=run.pk)

                # Execute immediately using existing execution path
                # Set status to running immediately to avoid race condition with UI polling
                run.status = "running"
                run.save(update_fields=["status"])
                run_workflow(wf, request.user, form.cleaned_data, run)
                return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=run.pk)

            elif execution_type == "scheduled":
                from .tasks import prepare_scheduled_workflow_task

                run.log = (
                    f"{run.log}\nPreparing scheduled workflow (running pre-actions before final scheduling)...".strip()
                    if run.log
                    else "Preparing scheduled workflow (running pre-actions before final scheduling)..."
                )
                run.save(update_fields=["status", "log"])

                prepare_scheduled_workflow_task.delay(run.pk)

                # Redirect immediately so user can watch pre-action progress in the run detail UI.
                if schedule_source == "servicenow":
                    messages.info(
                        request,
                        f"Scheduling requested for '{wf.name}' from ServiceNow change {schedule_change_ticket} "
                        f"at {scheduled_datetime.strftime('%Y-%m-%d %H:%M')} UTC. Pre-actions are now running.",
                    )
                else:
                    messages.info(
                        request,
                        f"Scheduling requested for '{wf.name}' at {scheduled_datetime.strftime('%Y-%m-%d %H:%M')} UTC. "
                        "Pre-actions are now running.",
                    )
                return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=run.pk)

        return render(request, "nautobot_workflow_engine/workflow_launch.html", {"workflow": wf, "form": form})


class InputOptionsView(View):
    """Return filtered options for a dependent object field."""

    def _build_enhanced_context(self, workflow, form_data):
        """Build context with both IDs and resolved object properties."""
        context = {"inputs": {}}

        for key, value in form_data.items():
            if not value:
                continue

            # Try to resolve object and add its properties
            try:
                wi = workflow.inputs.get(key=key)
                if wi.content_type:
                    model = wi.content_type.model_class()
                    obj = model.objects.get(pk=value)

                    # Add object with all useful properties
                    context["inputs"][key] = {
                        "id": obj.pk,
                        "name": getattr(obj, "name", str(obj)),
                        "slug": getattr(obj, "slug", None),
                        "description": getattr(obj, "description", None),
                        "__str__": str(obj),  # Default string representation
                    }

                    # Also make the object accessible for template rendering as just the ID (backward compatibility)
                    # This ensures "{{ inputs.tenant }}" still works for ID-based filters
                    context["inputs"][f"{key}_id"] = value
                else:
                    # Non-object field, just use the value
                    context["inputs"][key] = value
            except Exception:
                # Fall back to just the value if resolution fails
                context["inputs"][key] = value

        return context

    def get(self, request, key, child):
        """Get filtered options for a dependent field (object or choice with Jinja)."""
        qs = _get_workflow_queryset()
        wf = get_object_or_404(qs, key=key)
        try:
            wi = wf.inputs.get(key=child)
        except WorkflowInput.DoesNotExist:
            raise Http404

        # Handle object fields
        if wi.input_type in ("object", "multiobject") and wi.content_type:
            return self._handle_object_field(request, wf, wi)

        # Handle choice fields with Jinja templates
        if wi.input_type in ("choice", "multichoice") and wi.choices:
            return self._handle_choice_field(request, wf, wi)

        raise Http404

    def _handle_object_field(self, request, wf, wi):
        """Handle object field options with optional REST API search."""
        # Check if this is a server-side search request
        search_term = request.GET.get("search", "").strip()
        use_api_search = bool(search_term)

        if use_api_search:
            return self._handle_api_search(request, wf, wi, search_term)

        # For non-search requests, load first 200 entries using ORM
        return self._handle_orm_query(request, wf, wi, default_limit=200)

    def _handle_api_search(self, request, wf, wi, search_term):
        """Handle object field search using Nautobot REST API with proper authentication."""
        import requests

        try:
            # Get the model info
            app_label = wi.content_type.app_label

            api_endpoint = wi.content_type.model_class()._meta.verbose_name_plural.replace(" ", "-")

            # Build API URL
            api_path = f"/api/{app_label}/{api_endpoint}/"

            # Get the base URL
            base_url = request.build_absolute_uri("/").rstrip("/")
            api_url = f"{base_url}{api_path}"

            # Prepare API parameters
            params = {
                "q": search_term,
                "limit": int(request.GET.get("limit", 50)),
                "offset": int(request.GET.get("offset", 0)),
                "format": "json",
            }
            logger.info(f"Making API request to {api_url} with params: {params}")

            # Prepare headers with authentication
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Nautobot-Workflow-Engine/1.0",
            }

            # Add authentication headers from the original request
            if hasattr(request, "user") and request.user.is_authenticated:
                # For session-based auth, we need to include session cookies
                session_cookies = request.COOKIES

                # If we have a session cookie, use it
                if "sessionid" in session_cookies:
                    headers["Cookie"] = f"sessionid={session_cookies['sessionid']}"

                # Also add CSRF token if available
                if "csrftoken" in session_cookies:
                    headers["X-CSRFToken"] = session_cookies["csrftoken"]

            # Make the API request
            response = requests.get(api_url, params=params, headers=headers, timeout=120)
            response.raise_for_status()

            api_data = response.json()
            results = api_data.get("results", [])
            id_list = [item["id"] for item in results]
            model = wi.content_type.model_class()
            qs = model.objects.filter(id__in=id_list)

            # Apply additional filters from wi.filter_query
            context = self._build_enhanced_context(wf, request.GET)
            from .executors import _render

            rendered_filters = {}
            if wi.filter_query:
                try:
                    rendered_filters = _render(wi.filter_query, context, repo=wf.source_repo)
                except Exception as e:
                    logger.error(f"Error rendering filter query for API search: {e}")

            # Clean filters and apply
            clean_filters = {k: v for k, v in rendered_filters.items() if v not in (None, "")}
            if clean_filters:
                qs = qs.filter(**clean_filters)

            # Use distinct() to prevent duplicates from JOIN operations (e.g., filtering by related models)
            qs = qs.distinct()

            # Convert API response to our expected format
            data = []

            for obj in qs:
                display_name = (
                    getattr(obj, "display", None)
                    or getattr(obj, "display_name", None)
                    or getattr(obj, "name", None)
                    or getattr(obj, "label", None)
                    or getattr(obj, "prefix", None)
                    or getattr(obj, "address", None)
                    or getattr(obj, "hostname", None)
                    or str(obj)
                )
                data.append({"value": str(obj.pk), "label": display_name})

            response_data = {
                "options": data,
                "total_count": len(data),
                "has_more": bool(api_data.get("next")),
                "limit": params["limit"],
                "offset": params["offset"],
            }

            logger.info(f"API search returned {len(data)} results")
            return JsonResponse(response_data)

        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            # Fallback to ORM query
            return self._handle_orm_query(request, wf, wi)
        except Exception as e:
            logger.error(f"Error in API search: {e}")
            # Fallback to ORM query
            return self._handle_orm_query(request, wf, wi)

    def _handle_orm_query(self, request, wf, wi, default_limit=50):
        """Handle object field options using direct ORM queries."""
        # Build enhanced context with resolved objects
        context = self._build_enhanced_context(wf, request.GET)

        # Render filter queries with enhanced context
        if wi.filter_query:
            from .executors import _render

            try:
                rendered_filters = _render(wi.filter_query, context, repo=wf.source_repo)
            except Exception as e:
                logger.error(f"Error rendering filter query for {wi.key}: {e}")
                rendered_filters = {}
        else:
            rendered_filters = {}

        # Apply filters to queryset
        model = wi.content_type.model_class()
        qs = model.objects.all()
        if rendered_filters:
            # Filter out None values and empty strings
            clean_filters = {k: v for k, v in rendered_filters.items() if v not in (None, "")}
            if clean_filters:
                qs = qs.filter(**clean_filters)

        # Use distinct() to prevent duplicates from JOIN operations (e.g., filtering by related models like devices__isnull)
        qs = qs.distinct()

        # Handle pagination
        limit = int(request.GET.get("limit", default_limit))
        offset = int(request.GET.get("offset", 0))
        total_count = qs.count()

        # Apply pagination
        qs = qs[offset : offset + limit]

        # Use display field for consistency with API search results
        data = []
        for obj in qs:
            # Try to get a good display name, similar to API search logic
            display_name = (
                getattr(obj, "display", None)  # Nautobot's main display field
                or getattr(obj, "display_name", None)  # Alternative display field
                or getattr(obj, "name", None)  # Name field
                or getattr(obj, "label", None)  # Label field
                or getattr(obj, "prefix", None)  # For prefix objects
                or getattr(obj, "address", None)  # For IP address objects
                or getattr(obj, "hostname", None)  # For device objects
                or str(obj)  # Fallback to string representation
            )
            data.append({"value": str(obj.pk), "label": display_name})

        # Check if we're supposed to prepend a "Create New XXX" to the dropdown
        show_add_new_item = [x.lower() for x in PLUGIN_CFG.get("show_add_new_item", [])]
        if f"{wi.content_type.app_label}.{wi.content_type.name}" in show_add_new_item:
            data.insert(0, {"value": "__create__", "label": f"Create New {wi.content_type.name.title()}"})

        response_data = {
            "options": data,
            "total_count": total_count,
            "has_more": offset + limit < total_count,
            "limit": limit,
            "offset": offset,
        }

        return JsonResponse(response_data)

    def _handle_choice_field(self, request, wf, wi):
        """Handle choice field options with Jinja templating."""
        # Build enhanced context with resolved objects
        context = self._build_enhanced_context(wf, request.GET)

        try:
            import json

            from .executors import _render

            # Handle choices - could be a string (JSON) or already parsed list
            choices_value = wi.choices

            # If choices is a string, try to render it as Jinja template first
            if isinstance(choices_value, str):
                rendered_choices_str = _render(choices_value, context, repo=wf.source_repo)

                # Try to parse as JSON
                try:
                    rendered_choices = json.loads(rendered_choices_str)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse choices as JSON for {wi.key}: {e}")
                    logger.error(f"Choices string was: {rendered_choices_str}")
                    return JsonResponse({"options": []})
            else:
                # Choices is already a list/object, render it directly
                rendered_choices = _render(choices_value, context, repo=wf.source_repo)

            # Convert to options format
            if isinstance(rendered_choices, list):
                data = []
                for choice in rendered_choices:
                    if isinstance(choice, dict):
                        # Choice is a dict with value/label
                        data.append(
                            {"value": choice.get("value", ""), "label": choice.get("label", choice.get("value", ""))}
                        )
                    else:
                        # Choice is a simple value
                        data.append({"value": str(choice), "label": str(choice)})
            else:
                logger.error(f"Rendered choices is not a list for {wi.key}: {rendered_choices}")
                data = []

            return JsonResponse({"options": data})

        except Exception as e:
            logger.error(f"Error rendering choices for {wi.key}: {e}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            return JsonResponse({"options": []})


class WorkflowRunDetailView(ObjectView):
    """Detail view for individual workflow runs."""

    queryset = WorkflowRun.objects.select_related("workflow", "requested_by")
    template_name = "nautobot_workflow_engine/workflow_run.html"

    def get_extra_context(self, request, instance):
        """Add workflow availability context."""
        context = super().get_extra_context(request, instance)
        # Show workflow info in run view even if workflow is now disabled
        context["workflow_available"] = instance.workflow.enabled if instance.workflow else False
        return context


class WorkflowRunCancelView(View):
    """Cancel a scheduled workflow run before execution starts."""

    def post(self, request, pk):
        """Cancel only scheduled runs and mark them as canceled."""
        workflow_run = get_object_or_404(WorkflowRun.objects.select_related("workflow", "scheduled_job"), pk=pk)

        # Keep behavior consistent with other run views: hide inaccessible objects.
        if not request.user.has_perm("nautobot_workflow_engine.view_workflowrun", workflow_run):
            raise Http404

        if not request.user.has_perm("nautobot_workflow_engine.change_workflowrun", workflow_run):
            messages.error(request, "You do not have permission to cancel this workflow run.")
            return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=workflow_run.pk)

        scheduled_job = None
        with transaction.atomic():
            # Lock only the WorkflowRun row itself. Avoid joining nullable relations
            # with FOR UPDATE, which PostgreSQL rejects on outer-joined nullable sides.
            locked_run = WorkflowRun.objects.select_for_update().get(pk=workflow_run.pk)

            if locked_run.status == "canceled":
                messages.info(request, "This workflow run is already canceled.")
                return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=locked_run.pk)

            if locked_run.status not in ("scheduled", "pending_approval"):
                messages.info(request, "Only scheduled or pending-approval workflow runs can be canceled.")
                return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=locked_run.pk)

            scheduled_job = locked_run.scheduled_job
            canceled_at = timezone.now()
            canceled_msg = (
                f"Workflow run canceled by {request.user.username} on {canceled_at.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                "before execution."
            )

            locked_run.status = "canceled"
            locked_run.finished = canceled_at
            locked_run.scheduled_for = None
            locked_run.scheduled_job = None
            locked_run.log = f"{locked_run.log}\n{canceled_msg}".strip() if locked_run.log else canceled_msg
            locked_run.user_log = (
                f"{locked_run.user_log}\n{canceled_msg}".strip() if locked_run.user_log else canceled_msg
            )
            locked_run.save(update_fields=["status", "finished", "scheduled_for", "scheduled_job", "log", "user_log"])

        if scheduled_job:
            try:
                scheduled_job.enabled = False
                scheduled_job.save(update_fields=["enabled"])
            except Exception as exc:
                logger.warning("Unable to disable scheduled job %s for run %s: %s", scheduled_job.pk, pk, exc)

            try:
                scheduled_job.delete()
            except Exception as exc:
                logger.warning("Unable to delete scheduled job %s for run %s: %s", scheduled_job.pk, pk, exc)

        messages.success(request, "Scheduled workflow run canceled.")
        return redirect("plugins:nautobot_workflow_engine:workflow_run", pk=pk)


class WorkflowRunProgressView(View):
    """Return live progress data for a workflow run detail page."""

    def get(self, request, pk):
        """Return JSON payload for partial run-detail updates."""
        workflow_run = get_object_or_404(
            WorkflowRun.objects.select_related("workflow", "requested_by").prefetch_related("awx_runs"),
            pk=pk,
        )

        if not request.user.has_perm("nautobot_workflow_engine.view_workflowrun", workflow_run):
            raise Http404

        return JsonResponse(_serialize_workflow_run_progress(workflow_run))


class WorkflowRunListView(TemplateView):
    """List workflow execution history with filtering and pagination."""

    template_name = "nautobot_workflow_engine/workflow_run_list.html"

    def _get_filter_state(self):
        """Return persisted filter/page-size state, updated from current query parameters."""
        if hasattr(self, "_run_history_filter_state"):
            return self._run_history_filter_state

        session_state = self.request.session.get(RUN_HISTORY_SESSION_KEY, {}).copy()
        if not isinstance(session_state, dict):
            session_state = {}

        changed = False
        if self.request.GET.get("reset") in {"1", "true", "yes"}:
            session_state = {}
            changed = True

        for key in RUN_HISTORY_PERSISTED_KEYS:
            if key in self.request.GET:
                session_state[key] = self.request.GET.get(key, "")
                changed = True

        if changed:
            self.request.session[RUN_HISTORY_SESSION_KEY] = session_state
            self.request.session.modified = True

        self._run_history_filter_state = session_state
        return self._run_history_filter_state

    def _get_filter_value(self, key, default=""):
        """Return effective filter value from current request/session state."""
        state = self._get_filter_state()
        return state.get(key, default)

    def _get_per_page(self):
        """Validate and return page size requested by the user."""
        raw_per_page = self._get_filter_value("per_page", str(RUN_HISTORY_DEFAULT_PAGE_SIZE))
        try:
            per_page = int(raw_per_page)
        except (TypeError, ValueError):
            return RUN_HISTORY_DEFAULT_PAGE_SIZE

        return per_page if per_page in RUN_HISTORY_PAGE_SIZE_OPTIONS else RUN_HISTORY_DEFAULT_PAGE_SIZE

    def get_queryset(self):
        """Get workflow runs queryset with filtering."""
        from datetime import timedelta

        from django.utils import timezone
        from django.utils.dateparse import parse_datetime

        qs = WorkflowRun.objects.select_related("workflow", "requested_by").order_by("-started")
        permitted_run_ids = [
            wf.id for wf in qs if self.request.user.has_perm("nautobot_workflow_engine.view_workflowrun", wf)
        ]
        qs = qs.filter(id__in=permitted_run_ids)
        # Filter by workflow if specified
        workflow_key = self._get_filter_value("workflow", "")
        if workflow_key:
            qs = qs.filter(workflow__key=workflow_key)

        # Filter by status if specified
        status = self._get_filter_value("status", "")
        if status:
            qs = qs.filter(status=status)

        # Filter by user if specified
        user = self._get_filter_value("user", "")
        if user:
            qs = qs.filter(requested_by__username=user)

        # Optional category filter
        category = self._get_filter_value("category", "")
        if category:
            if category == "__uncategorized__":
                from django.db.models import Q

                qs = qs.filter(Q(workflow__category__isnull=True) | Q(workflow__category=""))
            else:
                qs = qs.filter(workflow__category=category)

        # Optional time range filter
        time_range = self._get_filter_value("time_range", "")
        start = self._get_filter_value("start", "")
        end = self._get_filter_value("end", "")
        now = timezone.now()
        start_dt = None
        end_dt = None
        if start and end:
            sdt = parse_datetime(start)
            edt = parse_datetime(end)
            if sdt and edt:
                start_dt = sdt if sdt.tzinfo else timezone.make_aware(sdt, dt_timezone.utc)
                end_dt = edt if edt.tzinfo else timezone.make_aware(edt, dt_timezone.utc)
        if not start_dt or not end_dt:
            if time_range:
                days = 0
                if time_range == "7d":
                    days = 7
                elif time_range == "30d":
                    days = 30
                elif time_range == "90d":
                    days = 90
                elif time_range == "365d":
                    days = 365
                if days:
                    start_dt = now - timedelta(days=days)
                    end_dt = now
        if start_dt and end_dt:
            qs = qs.filter(started__gte=start_dt, started__lte=end_dt)

        return qs

    def get_context_data(self, **kwargs):
        """Add context data for the template."""
        context = super().get_context_data(**kwargs)

        # Add queryset with pagination
        queryset = self.get_queryset()
        total_runs = queryset.count()

        per_page = self._get_per_page()
        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(self.request.GET.get("page"))

        context["object_list"] = page_obj.object_list
        context["workflow_runs"] = page_obj.object_list
        context["show_scheduled_for_column"] = queryset.filter(scheduled_for__isnull=False).exists()
        context["page_obj"] = page_obj
        context["paginator"] = paginator
        context["is_paginated"] = page_obj.has_other_pages()
        context["per_page"] = per_page
        context["per_page_options"] = RUN_HISTORY_PAGE_SIZE_OPTIONS
        context["total_runs"] = total_runs

        query_params = {
            "per_page": str(per_page),
            "workflow": self._get_filter_value("workflow", ""),
            "status": self._get_filter_value("status", ""),
            "user": self._get_filter_value("user", ""),
            "category": self._get_filter_value("category", ""),
            "time_range": self._get_filter_value("time_range", ""),
            "start": self._get_filter_value("start", ""),
            "end": self._get_filter_value("end", ""),
        }
        context["querystring_without_page"] = urlencode({k: v for k, v in query_params.items() if v not in (None, "")})

        # Add filter options
        context["workflows"] = _get_workflow_queryset().order_by("name")
        context["status_choices"] = WorkflowRun.STATUS_CHOICES

        # Get users who have executed workflows
        context["users"] = (
            WorkflowRun.objects.select_related("requested_by")
            .values("requested_by__username", "requested_by__pk")
            .distinct()
            .order_by("requested_by__username")
        )

        context["selected_workflow"] = self._get_filter_value("workflow", "")
        context["selected_status"] = self._get_filter_value("status", "")
        context["selected_user"] = self._get_filter_value("user", "")
        context["selected_category"] = self._get_filter_value("category", "")
        context["selected_time_range"] = self._get_filter_value("time_range", "")
        context["selected_start"] = self._get_filter_value("start", "")
        context["selected_end"] = self._get_filter_value("end", "")

        return context


class WorkflowReportView(TemplateView):
    """Dashboard view showing workflow execution statistics and recent runs."""

    template_name = "nautobot_workflow_engine/workflow_report.html"

    def get_context_data(self, **kwargs):
        """Add reporting data to context."""
        from datetime import timedelta

        from django.db.models import Q
        from django.db.models.functions import TruncDay, TruncWeek
        from django.utils import timezone
        from django.utils.dateparse import parse_datetime

        context = super().get_context_data(**kwargs)

        # Parse filters from GET
        selected_category = self.request.GET.get("category", "")
        selected_status = self.request.GET.get("status", "")
        selected_user = self.request.GET.get("user", "")
        selected_time_range = self.request.GET.get("time_range", "30d")
        selected_start = self.request.GET.get("start", "")
        selected_end = self.request.GET.get("end", "")

        # Compute time window
        now = timezone.now()
        start_dt = None
        end_dt = None
        if selected_start and selected_end:
            sdt = parse_datetime(selected_start)
            edt = parse_datetime(selected_end)
            if sdt and edt:
                start_dt = sdt if sdt.tzinfo else timezone.make_aware(sdt, dt_timezone.utc)
                end_dt = edt if edt.tzinfo else timezone.make_aware(edt, dt_timezone.utc)
        if not start_dt or not end_dt:
            # Fallback to presets
            days = 30
            if selected_time_range == "7d":
                days = 7
            elif selected_time_range == "90d":
                days = 90
            elif selected_time_range == "365d":
                days = 365
            start_dt = now - timedelta(days=days)
            end_dt = now

        # Build base queryset with permissions and filters
        base_qs = WorkflowRun.objects.select_related("workflow", "requested_by").filter(
            started__gte=start_dt, started__lte=end_dt
        )
        permitted_run_ids = [
            wf.id for wf in base_qs if self.request.user.has_perm("nautobot_workflow_engine.view_workflowrun", wf)
        ]
        base_qs = base_qs.filter(id__in=permitted_run_ids)

        # Apply category filter
        if selected_category:
            if selected_category == "__uncategorized__":
                base_qs = base_qs.filter(Q(workflow__category__isnull=True) | Q(workflow__category=""))
            else:
                base_qs = base_qs.filter(workflow__category=selected_category)

        # Apply status filter
        if selected_status:
            base_qs = base_qs.filter(status=selected_status)

        # Apply user filter (handle UUID or integer PKs; avoid casting)
        if selected_user:
            # Compare PK as string to support UUID-based User IDs
            base_qs = base_qs.filter(requested_by__pk=str(selected_user))

        # Status summary (filtered scope)
        status_summary = base_qs.values("status").annotate(count=Count("id")).order_by("status")
        context["status_summary"] = status_summary

        # Workflow popularity (filtered scope)
        workflow_summary = (
            base_qs.values("workflow__name", "workflow__key").annotate(count=Count("id")).order_by("-count")[:10]
        )
        context["workflow_summary"] = workflow_summary

        # Category usage summary
        category_summary = base_qs.values("workflow__category").annotate(count=Count("id")).order_by("-count")
        context["category_summary"] = category_summary

        # Top users summary
        user_summary = (
            base_qs.values("requested_by__username", "requested_by__pk")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        context["user_summary"] = user_summary

        # Success rate by workflow within filtered scope
        workflows_with_stats = []
        for workflow in _get_workflow_queryset():
            wf_runs = base_qs.filter(workflow=workflow)
            total_runs = wf_runs.count()
            successful_runs = wf_runs.filter(status="success").count()
            success_rate = (successful_runs / total_runs * 100.0) if total_runs else 0.0
            workflows_with_stats.append(
                {
                    "workflow": workflow,
                    "total_runs": total_runs,
                    "successful_runs": successful_runs,
                    "success_rate": success_rate,
                }
            )
        workflows_with_stats.sort(key=lambda x: x["total_runs"], reverse=True)
        context["workflows_with_stats"] = workflows_with_stats[:10]

        # Time savings calculation (filtered scope)
        time_savings_data = self._calculate_time_savings(base_qs.filter(status="success"))
        context["time_savings"] = time_savings_data

        # Monthly time savings (last 12 months; keep existing behavior)
        context["monthly_time_savings"] = self._calculate_monthly_time_savings()

        # Success vs failure by category breakdown
        success_by_category_raw = base_qs.values("workflow__category", "status").annotate(count=Count("id"))
        success_by_category = {}
        for row in success_by_category_raw:
            cat = row["workflow__category"] or "Uncategorized"
            success_by_category.setdefault(
                cat,
                {
                    "success": 0,
                    "failed": 0,
                    "running": 0,
                    "pending": 0,
                    "scheduled": 0,
                    "canceled": 0,
                    "partial": 0,
                },
            )
            success_by_category[cat][row["status"]] = row["count"]
        success_by_category_list = []
        for cat, counts in success_by_category.items():
            total = sum(counts.values())
            success_rate = (counts.get("success", 0) / total * 100.0) if total else 0.0
            success_by_category_list.append(
                {"category": cat, "counts": counts, "total": total, "success_rate": success_rate}
            )
        success_by_category_list.sort(key=lambda x: x["total"], reverse=True)
        context["success_by_category"] = success_by_category_list

        # Usage over time buckets (daily if <=90 days, else weekly)
        window_days = max((end_dt - start_dt).days, 1)
        if window_days > 90:
            time_buckets = (
                base_qs.annotate(bucket=TruncWeek("started"))
                .values("bucket")
                .annotate(count=Count("id"))
                .order_by("bucket")
            )
        else:
            time_buckets = (
                base_qs.annotate(bucket=TruncDay("started"))
                .values("bucket")
                .annotate(count=Count("id"))
                .order_by("bucket")
            )
        usage_over_time = [
            {"label": b["bucket"].strftime("%Y-%m-%d"), "count": b["count"]} for b in time_buckets if b.get("bucket")
        ]
        context["usage_over_time"] = usage_over_time

        # Filter options for template controls
        all_categories = (
            _get_workflow_queryset()
            .exclude(category="")
            .exclude(category__isnull=True)
            .values_list("category", flat=True)
            .distinct()
            .order_by("category")
        )
        context["categories"] = list(all_categories)
        context["status_choices"] = WorkflowRun.STATUS_CHOICES
        context["users"] = (
            WorkflowRun.objects.select_related("requested_by")
            .values("requested_by__username", "requested_by__pk")
            .distinct()
            .order_by("requested_by__username")
        )

        # Selected values for form persistence
        context["selected_category"] = selected_category
        context["selected_status"] = selected_status
        context["selected_user"] = selected_user
        context["selected_time_range"] = selected_time_range
        context["selected_start"] = selected_start
        context["selected_end"] = selected_end

        # Human-readable window label for templates
        if selected_start and selected_end and start_dt and end_dt and selected_time_range == "custom":
            context["window_label"] = f"{start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}"
        else:
            range_map = {
                "7d": "Last 7 Days",
                "30d": "Last 30 Days",
                "90d": "Last 90 Days",
                "365d": "Last 365 Days",
            }
            context["window_label"] = range_map.get(selected_time_range, "Selected Period")

        return context

    def _calculate_time_savings(self, recent_runs):
        """Calculate time savings from workflow automation over the last 30 days."""
        # Get runs with manual duration data and calculate totals
        runs_with_duration = recent_runs.select_related("workflow").filter(
            workflow__manual_duration_minutes__isnull=False
        )

        # Calculate total manual time that would have been required
        total_manual_minutes = 0
        total_automation_minutes = 0
        runs_count = 0

        for run in runs_with_duration:
            if run.workflow.manual_duration_minutes:
                total_manual_minutes += run.workflow.manual_duration_minutes
                runs_count += 1

                # Calculate actual workflow execution time and measured form-fill duration
                if run.started and run.finished and run.finished >= run.started:
                    execution_seconds = int((run.finished - run.started).total_seconds())
                    # Fallback to 60s (1 minute) for legacy runs without measured form duration
                    form_seconds = 60 if run.form_duration_seconds in (None, "") else int(run.form_duration_seconds)
                    total_automation_minutes += ceil((execution_seconds + form_seconds) / 60.0)

        # Calculate savings
        time_saved_minutes = total_manual_minutes - total_automation_minutes

        return {
            "total_manual_minutes": total_manual_minutes,
            "total_automation_time": total_automation_minutes,
            "total_automation_minutes": total_automation_minutes % 60,
            "total_automation_hours": floor(total_automation_minutes / 60),
            "time_saved_minutes": time_saved_minutes % 60,
            "time_saved_hours": floor(time_saved_minutes / 60),
            "time_saved_total_minutes": time_saved_minutes,
            "runs_with_duration_count": runs_count,
        }

    def _calculate_monthly_time_savings(self):
        """Aggregate time saved per month over the last 12 months.

        Returns a list of dicts sorted by month ascending (oldest to newest) with:
        - month_label: e.g. 'Jan 2025'
        - total_minutes: total time saved in that month (manual - automation)
        - hours, minutes: breakdown of total_minutes
        - percent_of_max: percent relative to maximum monthly saved minutes in the period
        """
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        # Start from beginning of current month
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Collect month boundaries (12 months back including current)
        month_starts = []
        cursor = current_month_start
        for _ in range(12):
            month_starts.append(cursor)
            # Move back one month: subtract one day from the first of month then set day=1
            prev_month_last_day = cursor - timedelta(days=1)
            cursor = prev_month_last_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Reverse to oldest -> newest
        month_starts.reverse()

        # Helper to get next month start
        def next_month(dt):
            if dt.month == 12:
                return dt.replace(year=dt.year + 1, month=1)
            return dt.replace(month=dt.month + 1)

        monthly = []
        # Pre-fetch successful runs in the 12-month span for efficiency
        earliest_start = month_starts[0]
        all_runs = WorkflowRun.objects.filter(started__gte=earliest_start, status="success").select_related("workflow")

        for start in month_starts:
            end = next_month(start)
            runs = [
                r for r in all_runs if r.started >= start and r.started < end and r.workflow.manual_duration_minutes
            ]
            total_manual = 0
            total_automation = 0
            for run in runs:
                total_manual += run.workflow.manual_duration_minutes or 0
                if run.started and run.finished and run.finished >= run.started:
                    execution_seconds = int((run.finished - run.started).total_seconds())
                    form_seconds = 60 if run.form_duration_seconds in (None, "") else int(run.form_duration_seconds)
                    total_automation += ceil((execution_seconds + form_seconds) / 60.0)
            time_saved = max(total_manual - total_automation, 0)
            monthly.append(
                {
                    "month_label": start.strftime("%b %Y"),
                    "total_minutes": time_saved,
                    "hours": floor(time_saved / 60),
                    "minutes": time_saved % 60,
                }
            )

        max_saved = max((m["total_minutes"] for m in monthly), default=0)
        for m in monthly:
            if max_saved > 0:
                m["percent_of_max"] = (m["total_minutes"] / max_saved) * 100
            else:
                m["percent_of_max"] = 0

            # Determine a simple CSS class for coloring in the template
            pct = m["percent_of_max"]
            if pct >= 70:
                m["bar_class"] = "best"
            elif pct >= 40:
                m["bar_class"] = "high"
            elif pct >= 20:
                m["bar_class"] = "med"
            else:
                m["bar_class"] = "low"

        return monthly
