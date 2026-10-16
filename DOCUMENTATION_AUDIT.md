# Documentation Gap Audit & Rectification Plan

**Scope.** Compare the app's implemented capabilities (from code) against the current
documentation set, note the gaps, and lay out a plan to close them.

**Method.**

1. Enumerated the feature surface from code: `models.py`, `views.py`, `urls.py`,
   `navigation.py`, `jobs.py`, `tasks.py`, `datasources.py`, `executors.py`,
   `scheduling.py`, `servicenow.py`, templates, and `PLUGINS_CONFIG` settings.
2. Read every page in the current `mkdocs.yml` navigation (index, user, admin,
   dev) and cross-referenced each capability.
3. Classified each capability as **Covered**, **Partial**, or **Missing**.

---

## 1. Capability → Documentation map

| Capability (code) | Where in code | Documented | Status |
| --- | --- | --- | --- |
| Define workflow YAML + action script | `datasources.py`, `models.py` | `user/app_getting_started.md` | Covered |
| Input types (string/integer/boolean/choice/multichoice/object/multiobject/list/listdict) | `models.py` `WorkflowInput` | `user/app_use_cases.md` | Covered |
| Action phases (pre/main/post), nautobot/awx | `models.py` `WorkflowAction` | `user/app_use_cases.md` | Covered |
| Reusable inputs, `{% include %}`, dynamic choices | `datasources.py`, `executors.py` | `user/app_use_cases.md` | Covered |
| Script contract + `scripts/lib/` helpers | `executors.py` | `user/app_use_cases.md` | Covered |
| Custom Jinja filters (repo-backed) | `WorkflowFilterModule` | `dev/extending.md` | Covered (dev location) |
| AWX integration + job monitoring | `external_interactions`, `executors.py` | `user/external_interactions.md` | Covered |
| ServiceNow change windows + sync job | `servicenow.py`, `jobs.py` | `user/external_interactions.md` | Covered |
| Installation, configuration reference | `__init__.py` `PLUGINS_CONFIG` | `admin/install.md` | Covered |
| Permissions (by name/category/entirely) + dashboard scoping | `executors.py`, `views.py` | `admin/install.md`, `admin/security.md` | Covered |
| Background jobs (executor, orphan cleaner, ServiceNow sync) | `jobs.py` | `admin/install.md` | Covered |
| Enable / disable without losing history | `models.py`, `datasources.py` | `admin/install.md`, `app_use_cases.md` | Covered |
| Upgrade / uninstall / compatibility | — | `admin/*.md` | Covered |
| **Reporting dashboard (metrics, filters)** | `views.py` `WorkflowReportView` | only "dashboard statistics" in `admin/release_notes/version_1.0.md` | **Missing** |
| **Dashboard is permission-aware** | `views.py` (filters to permitted `WorkflowRun`) | implied only | **Missing** |
| **Time savings (`manual_duration_minutes`, total + monthly 12-month)** | `models.py`, `views.py` `_calculate_time_savings*` | one line in `app_use_cases.md` | **Partial** |
| **Scheduling modes in depth (immediate / scheduled datetime / ServiceNow; pre-actions before final scheduling)** | `scheduling.py`, `tasks.py`, `jobs.py` | brief section in `app_use_cases.md` | **Partial** |
| **Live run progress (progress endpoint + progress UI)** | `views.py` `WorkflowRunProgressView`, `urls.py` | — | **Missing** |
| **Run status lifecycle reference (scheduled/pending/running/success/failed/partial/canceled)** | `models.py` `WorkflowRun.STATUS_CHOICES` | only cancel-related lines in `faq.md` | **Partial** |
| **Conditional action gate (`when`)** | `models.py` `WorkflowAction.when` | phases covered, `when` not | **Partial** |
| **REST API (`WorkflowUIViewSet`)** | `urls.py` router | only dev code reference | **Missing** (optional) |

---

## 2. Missing / under-documented items (notes)

**HIGH**

1. **Reporting dashboard — no dedicated page.** `WorkflowReportView` computes a
   rich set of metrics that no user-facing page explains:
    - Status summary (counts by status)
    - Workflow popularity (top 10 by run count)
    - Category usage summary
    - Top users
    - Success rate by workflow
    - Time savings (total, from successful runs)
    - Monthly time savings (last 12 months)
    - Success vs. failure by category
    - Usage over time (daily buckets ≤ 90 days, weekly beyond)
   Filters: category, status, user, time range (7/30/90/365d), start/end date.
   Only "dashboard statistics" is named in the release notes.

2. **The dashboard is permission-aware — not documented anywhere.** The view
   filters its base queryset to the `WorkflowRun` objects the viewer has
   `view_workflowrun` permission for, so every metric only reflects the runs the
   viewer can see. This is a key security/behavior property (see
   `admin/install.md` "Permissions") but the dashboard page never states it.

3. **Time savings (`manual_duration_minutes`) — under-documented.** The field
   exists on both `Workflow` and `WorkflowRun`, and the dashboard derives total
   and 12-month time savings from it, but there is no page explaining:
   where to set it, that it is an *estimate* of manual effort (not actual
   execution time), how total vs. monthly savings are computed, and that only
   successful runs count toward time savings.

**MEDIUM**

4. **Scheduling modes in depth — partial.** `app_use_cases.md` names "immediate or
   scheduled execution" with an optional ServiceNow change window, but does not
   explain the three modes or the behavior that pre-actions run *before* the
   run is finally scheduled (`prepare_scheduled_workflow_task`), or the status
   transitions a scheduled run goes through.

5. **Live run progress — missing.** `WorkflowRunProgressView`
   (`/runs/<pk>/progress/`) drives the run-detail progress bar (percent + label
   by status, including AWX job completion and elapsed time). No doc describes
   that a run detail view shows live progress.

**LOW**

6. **Run status lifecycle reference — partial.** The full status set
   (`scheduled, pending, running, success, failed, partial, canceled`) has no
   reference table; only cancel-related facts appear in `faq.md`.

7. **Conditional action gate (`when`) — partial.** `WorkflowAction.when` lets a
   phase/action run conditionally; phases are documented but `when` is not.

8. **REST API (`WorkflowUIViewSet`) — missing (optional).** A Workflow REST
   viewset is registered but has no user-facing usage docs (only the dev code
   reference exists).

---

## 3. Rectification plan (prioritized)

> Note: this plan defines the work; it is the deliverable requested. The new
> pages below are to be authored in a follow-up, not written here.

**P0 — Reporting dashboard page (closes items 1, 2, 3).**

- New page `docs/user/app_reporting.md` (nav: add "Reporting Dashboard" under the
  user section, linked from `app_overview.md`).
- Sections:
    1. What the dashboard shows (one line per metric, matching the code).
    2. Filters (category, status, user, time range presets, custom start/end).
    3. **Permission-awareness** — state clearly that all figures are computed only
       over the runs the viewer has `view_workflowrun` permission for; cross-link
       `admin/install.md#permissions`.
    4. **Time savings** — where to set `manual_duration_minutes` (per workflow and
       per run), that it is an estimate, how total and 12-month figures are
       derived, and that only `success` runs count.

**P1 — Expand scheduling (closes item 4).**

- In `docs/user/app_use_cases.md`, expand "Scheduling and Workflow Lifecycle" to
  describe the three modes (immediate, scheduled datetime, ServiceNow change
  window) and that pre-actions run before the run is finally scheduled.

**P2 — Run lifecycle + progress (closes items 5, 6).**

- Add a short "Run lifecycle" section (to `app_use_cases.md` or `faq.md`) with a
  status table for the six statuses and a note that run detail shows live
  progress (percent/label, AWX job completion, elapsed time).

**P3 — Minor completeness (closes items 7, 8).**

- Add a line on the `when` conditional action gate in `app_use_cases.md`.
- Optionally add a short "REST API" note (or a page) for `WorkflowUIViewSet`.

**Sequencing / effort.**

- P0 is the highest value and self-contained; do it first (new page, nav entry,
  and one cross-link). ~ medium effort.
- P1 and P2 are edits to existing pages; independent of P0. ~ low effort each.
- P3 is optional polish. ~ low effort.
- After authoring, run `mkdocs build` and confirm the new nav entries render and
  no broken internal links remain.
