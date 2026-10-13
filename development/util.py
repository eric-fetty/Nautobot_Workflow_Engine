"""Utilities for generating sample workflow run data in development environments."""

from __future__ import annotations

import argparse
from datetime import timedelta
from random import SystemRandom
from typing import List

import django
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

RNG = SystemRandom()


def _get_models():
    """Initialize Django and return the workflow models used by this utility."""
    django.setup()

    from nautobot_workflow_engine.models import Workflow, WorkflowRun

    return Workflow, WorkflowRun


def month_starts(now, months: int):
    """Return month-start timestamps from oldest to newest for the requested window."""
    base = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # oldest -> newest
    return [base - relativedelta(months=offset) for offset in range(months - 1, -1, -1)]


def populate_sample_runs(
    months: int = 12,
    min_per_month: int = 3,
    max_per_month: int = 8,
    clear: bool = False,
    sample_marker: str = "[SAMPLE_DATA]",
    dry_run: bool = False,
) -> int:
    """Create or preview sample workflow runs spread across recent months."""
    Workflow, WorkflowRun = _get_models()
    User = get_user_model()
    user = User.objects.first() or User.objects.create_user("demo", "demo@example.com", "demo")

    workflows = list(Workflow.objects.all())
    if not workflows:
        Workflow.objects.bulk_create(
            [
                Workflow(
                    key="demo-provision",
                    name="Demo Provision",
                    enabled=True,
                    raw_definition={},
                    manual_duration_minutes=45,
                ),
                Workflow(
                    key="demo-move", name="Demo Move", enabled=True, raw_definition={}, manual_duration_minutes=30
                ),
                Workflow(
                    key="demo-decommission",
                    name="Demo Decommission",
                    enabled=True,
                    raw_definition={},
                    manual_duration_minutes=20,
                ),
            ]
        )
        workflows = list(Workflow.objects.filter(key__in=["demo-provision", "demo-move", "demo-decommission"]))

    if clear:
        WorkflowRun.objects.filter(user_log__icontains=sample_marker).delete()

    now = timezone.now()
    starts = month_starts(now, months)
    pending_instances: List[WorkflowRun] = []

    # First create with default started (auto_now_add); we’ll patch after save.
    for month_start in starts:
        next_start = month_start + relativedelta(months=1)
        days_in_month = (next_start - month_start).days
        count = RNG.randint(min_per_month, max_per_month)
        for _ in range(count):
            wf = RNG.choice(workflows)
            day_offset = RNG.randint(0, days_in_month - 1)
            hour = RNG.randint(0, 23)
            minute = RNG.randint(0, 59)
            intended_started = month_start + timedelta(days=day_offset, hours=hour, minutes=minute)
            if intended_started > now:
                intended_started = now - timedelta(minutes=RNG.randint(1, 30))

            manual_minutes = wf.manual_duration_minutes or RNG.randint(15, 90)
            actual_minutes = max(1, int(RNG.gauss(manual_minutes * 0.3, max(2, manual_minutes * 0.1))))

            status = RNG.choices(
                ["success", "failed", "partial", "running", "pending"],
                weights=[55, 10, 5, 15, 15],
                k=1,
            )[0]

            # Placeholder; will adjust finished after start patch.
            finished = None
            if status in ("success", "failed", "partial"):
                finished = intended_started + timedelta(minutes=actual_minutes)
                if finished > now:
                    finished = now - timedelta(seconds=RNG.randint(5, 300))

            run = WorkflowRun(
                workflow=wf,
                requested_by=user,
                inputs={},
                status=status,
                log=f"{sample_marker} Simulated run for {wf.key}.",
                user_log=f"{sample_marker} Manual est: {manual_minutes}m, actual: {actual_minutes}m",
            )
            # Store intended times for post-save update
            run._intended_started = intended_started  # type: ignore
            run._intended_finished = finished  # type: ignore
            pending_instances.append(run)

    if dry_run:
        summary = {}
        for r in pending_instances:
            key = r._intended_started.strftime("%Y-%m")  # type: ignore
            summary[key] = summary.get(key, 0) + 1
        print(f"[DRY-RUN] Would create {len(pending_instances)} runs.")
        for k in sorted(summary):
            print(f"{k}: {summary[k]} runs")
        return len(pending_instances)

    # Persist then patch timestamps
    with transaction.atomic():
        for r in pending_instances:
            r.save()  # started auto-populated
        # Patch started/finished explicitly
        for r in pending_instances:
            WorkflowRun.objects.filter(pk=r.pk).update(
                started=r._intended_started,  # type: ignore
                finished=r._intended_finished,
            )

    return len(pending_instances)


def _parse_args():
    """Parse CLI arguments for the sample data generation utility."""
    p = argparse.ArgumentParser(description="Populate sample WorkflowRun objects (demo).")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--min-per-month", type=int, default=3)
    p.add_argument("--max-per-month", type=int, default=8)
    p.add_argument("--clear", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    created = populate_sample_runs(
        months=args.months,
        min_per_month=args.min_per_month,
        max_per_month=args.max_per_month,
        clear=args.clear,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f"[DRY-RUN] Would create {created} runs.")
    else:
        print(f"Created {created} runs.")
