"""
allocation_reports/views.py
==============================
Wiring summary (see urls.py for the exact paths):

  /allocations/cod/                 -> cod_allocation_panel
      COD / COD Admin: sees ONLY their own department, for every scope
      (main/odel/campus/resit). Generated synchronously & isolated per
      department — one COD can never trigger or see another department's
      run through this view.

  /allocations/cod/all/             -> cod_all_departments_view
      Same COD/COD-Admin users, but "can see all the allocations too" per
      spec — read-only cross-department view (reuses the management
      dashboard template in read-only mode, no generation triggers).

  /allocations/dvc/                 -> management_dashboard (scope switch via ?scope=)
  /allocations/dean/                -> management_dashboard
  /allocations/timetable-dashboard/ -> management_dashboard
      DVC / Dean / Director-Timetable / Timetable Admin / Sudo: shows every
      department for the selected scope. Missing/stale PDFs are queued in
      background threads on page load; the page polls api_status() and
      swaps in the finished PDF without a full reload.

  /allocations/resit/generate/      -> generate_resit_allocation
      The "Generate Resit Allocation" button target. Redirects into the
      resit scope of whichever dashboard the user is allowed to see.

  /allocations/api/status/          -> api_status
      JSON poll endpoint used by the dashboard's JS.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from core.rbac import Role, allowed_roles, resolve_user_department
from department_management.models import Department

from allocation_reports.adapters import ADAPTERS
from allocation_reports.models import AllocationPdfRun
from allocation_reports.services import (
    dashboard_rows, get_or_generate_pdf, queue_bulk_generation,
)

SCOPES = list(ADAPTERS.keys())

MANAGEMENT_ROLES_FOR_ALLOCATIONS = (
    Role.DVC, Role.DVC_ADMIN, Role.DEAN, Role.DEAN_ADMIN,
    Role.DIRECTOR, Role.TIMETABLE_ADMIN, Role.TIMETABLER, Role.SUDO,
)


def _clean_scope(request, default="main"):
    scope = request.GET.get("scope", default)
    return scope if scope in SCOPES else default


# ─────────────────────────────────────────────────────────────────────────────
# COD — own department only
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def cod_allocation_panel(request):
    department = resolve_user_department(request.user)
    if department is None and not request.user.is_superuser:
        raise Http404("No department is linked to your account. Contact the timetabling office.")

    if request.user.is_superuser and department is None:
        # Sudo browsing without a linked department picks one explicitly.
        dept_id = request.GET.get("department")
        department = Department.objects.filter(pk=dept_id).first() if dept_id else Department.objects.first()

    panels = []
    for scope in SCOPES:
        run = get_or_generate_pdf(scope, department, user=request.user)
        history = (
            AllocationPdfRun.objects
            .filter(scope=scope, department=department)
            .order_by("-version")[:10]
        )
        panels.append({
            "scope": scope,
            "label": ADAPTERS[scope].label,
            "current": run,
            "history": history,
        })

    return render(request, "allocation_reports/cod_panel.html", {
        "department": department,
        "panels": panels,
    })


@login_required
@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def cod_all_departments_view(request):
    """COD can also browse every department's allocations, read-only."""
    scope = _clean_scope(request)
    queue_bulk_generation(scope, user=request.user)
    rows = dashboard_rows(scope)
    return render(request, "allocation_reports/dashboard.html", {
        "scope": scope,
        "scope_label": ADAPTERS[scope].label,
        "scopes": SCOPES,
        "rows": rows,
        "read_only": True,
        "base_url": reverse("cod_all_departments_allocations"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# DVC / Dean / Timetable dashboard — every department, background generation
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@allowed_roles(*MANAGEMENT_ROLES_FOR_ALLOCATIONS)
def management_dashboard(request):
    scope = _clean_scope(request)

    # Kick off background generation for anything missing/stale; returns
    # instantly — the page shows cached PDFs immediately and polls for the
    # rest so opening this page never blocks on a slow bulk render.
    queue_bulk_generation(scope, user=request.user)

    rows = dashboard_rows(scope)
    return render(request, "allocation_reports/dashboard.html", {
        "scope": scope,
        "scope_label": ADAPTERS[scope].label,
        "scopes": SCOPES,
        "rows": rows,
        "read_only": False,
        "base_url": reverse("management_allocations_dashboard"),
    })


@login_required
@allowed_roles(*MANAGEMENT_ROLES_FOR_ALLOCATIONS)
def force_regenerate(request, scope, department_id):
    if scope not in SCOPES:
        raise Http404("Unknown scope")
    department = Department.objects.filter(pk=department_id).first()
    if department is None:
        raise Http404("Department not found")
    get_or_generate_pdf(scope, department, user=request.user, force=True)
    next_url = request.GET.get("next") or reverse("management_allocations_dashboard")
    return redirect(f"{next_url}?scope={scope}")


# ─────────────────────────────────────────────────────────────────────────────
# Resit "Generate Resit Allocation" button
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@allowed_roles(Role.COD, Role.COD_ADMIN, *MANAGEMENT_ROLES_FOR_ALLOCATIONS)
def generate_resit_allocation(request):
    """
    Target of the "Generate Resit Allocation" panel button.
    COD/COD Admin -> their own department's resit PDF (isolated).
    DVC/Dean/Timetable/Sudo -> the cross-department resit dashboard,
    generating in the background for every department that has resit data.
    """
    user = request.user
    from core.rbac import get_user_roles
    roles = get_user_roles(user)

    if roles & {Role.COD, Role.COD_ADMIN} and not (roles & set(MANAGEMENT_ROLES_FOR_ALLOCATIONS)):
        department = resolve_user_department(user)
        if department is None:
            raise Http404("No department linked to your account.")
        get_or_generate_pdf(AllocationPdfRun.SCOPE_RESIT, department, user=user, force=True)
        return redirect(f"{reverse('cod_allocations_panel')}#resit")

    queue_bulk_generation(AllocationPdfRun.SCOPE_RESIT, user=user)
    return redirect(f"{reverse('management_allocations_dashboard')}?scope=resit")


# ─────────────────────────────────────────────────────────────────────────────
# JSON polling endpoint (no page reload needed while background jobs finish)
# ─────────────────────────────────────────────────────────────────────────────
@login_required
def api_status(request):
    scope = request.GET.get("scope")
    department_id = request.GET.get("department_id")
    if scope not in SCOPES or not department_id:
        return JsonResponse({"error": "scope and department_id are required"}, status=400)

    department = Department.objects.filter(pk=department_id).first()
    if department is None:
        return JsonResponse({"error": "department not found"}, status=404)

    from allocation_reports.services import is_generating
    from allocation_reports.signature import compute_signature

    run = (
        AllocationPdfRun.objects
        .filter(scope=scope, department=department, is_current=True)
        .first()
    )
    _, row_count = compute_signature(scope, department)

    return JsonResponse({
        "scope": scope,
        "department_id": department.pk,
        "generating": is_generating(scope, department.pk),
        "status": run.status if run else "pending",
        "row_count": row_count,
        "ready": bool(run and run.is_ready),
        "failed": bool(run and run.status == AllocationPdfRun.STATUS_FAILED),
        "error_message": run.error_message if run else None,
        "url": (run.file.url if run and run.file else None),
        "version": run.version if run else None,
        "generated_at": run.generated_at.isoformat() if run and run.generated_at else None,
    })
