from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from course_allocation.models import AllocationSet
from course_allocation.allocation_scope import (
    get_tt_active_allocation_set_ids,
    set_tt_active_allocation_set_ids,
)


@login_required
def timetable_dashboard_view(request):
    """
    Main timetable dashboard view with Django messages support.

    This is also the single entry point where the Timetabling Office picks
    which actual AllocationSet(s) -- across every department -- every other
    TT-side page should work against for the rest of the session: the
    class/lab/exam timetable panels, the autoschedulers, and so on. Tick
    one per department (or several, or none for a department that isn't
    ready yet), Apply, and that selection is stored in-session
    (course_allocation.allocation_scope) and read from there by those
    pages; none of them has its own switcher yet, so whatever is ticked
    here is what they all use until that lands.
    """
    if request.method == "POST" and "apply_allocation_sets" in request.POST:
        picked_ids = request.POST.getlist("allocation_set_ids")
        stored_ids = set_tt_active_allocation_set_ids(request, picked_ids)

        if stored_ids:
            sets = list(
                AllocationSet.objects.filter(id__in=stored_ids).select_related("department")
            )
            names = ", ".join(f"{s.department.name} — {s.name}" for s in sets)
            messages.success(
                request,
                f"Switched to {len(stored_ids)} allocation set(s): {names}. "
                "This applies to every timetable panel and autoscheduler for the rest of your session."
            )
        else:
            messages.info(
                request,
                "No allocation sets selected — every timetable panel and autoscheduler will fall back to "
                "the default (not-yet-set / legacy / submitted-to-TT) allocations for the rest of your session."
            )
        return redirect("timetable_dashboard")

    departments_with_sets = (
        AllocationSet.objects
        .filter(is_archived=False)
        .select_related("department")
        .order_by("department__name", "-created_at")
    )

    # Group by department for the picker UI.
    grouped = {}
    for allocation_set in departments_with_sets:
        grouped.setdefault(allocation_set.department, []).append(allocation_set)

    active_ids = set(get_tt_active_allocation_set_ids(request))

    return render(request, "dashboard/timetabling_dashboard.html", {
        "allocation_sets_by_department": sorted(grouped.items(), key=lambda kv: kv[0].name),
        "active_allocation_set_ids": active_ids,
    })
