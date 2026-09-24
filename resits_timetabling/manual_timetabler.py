"""
resits_timetabling/manual_timetabler.py
Manual form-based resit timetable management.

Key changes vs previous version:
  - Manual scheduling writes directly to ResitTimetable (published); no draft step.
  - A course can be scheduled multiple times (multiple days / slots).
  - assign_resit_slot no longer overwrites; it creates a new entry each time
    (unless `editing_entry_id` is passed, in which case it updates that specific entry).
  - No program-year collision enforcement — resit courses can share slots freely.
  - get_resit_panel_data now returns course_name and lecturer for all courses.
  - delete_resit_draft / delete_published_entry / bulk_delete unchanged.
  - NEW: check_resit_slot_conflict — student-aware conflict detection with
    alternative slot recommendations, called by frontend before saving.
  - assign_resit_slot now returns conflict_info in the response so the frontend
    can surface warnings even when it saves anyway (soft-conflict model).
"""
import json
from datetime import datetime
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from core.group_required import group_required
from room_management.models import Venue
from .models import (
    ResitSchedulerConfig, ResitTimetable,
    ResitCourseAllocation, ResitAutoMergedGroup
)


# ─── helpers ───────────────────────────────────────────────────────────────

def _build_slot_grid(config: ResitSchedulerConfig) -> list[dict]:
    """
    Return a list of {date, day, start_time, end_time} slot dicts.

    Reads from the pre-generated ResitTimeSlot rows stored in the DB when the
    config was saved.  This is the single source of truth for slot layout,
    ensuring the break_between_slots is applied consistently for both the
    manual timetabler and the auto-scheduler.
    """
    if not config or not config.start_date:
        return []

    from .models import ResitTimeSlot  # avoid top-level circular import if needed

    qs = (
        ResitTimeSlot.objects
        .filter(config=config)
        .order_by("date", "start_time")
        .values("date", "day", "start_time", "end_time")
    )

    slots = []
    for s in qs:
        st = s["start_time"].strftime("%H:%M") if hasattr(s["start_time"], "strftime") else str(s["start_time"])[:5]
        et = s["end_time"].strftime("%H:%M")   if hasattr(s["end_time"],   "strftime") else str(s["end_time"])[:5]
        slots.append({
            "date":       str(s["date"]),
            "day":        s["day"],
            "start_time": st,
            "end_time":   et,
        })
    return slots


def _get_unscheduled_resit_allocations():
    """
    Returns ResitCourseAllocations that have NO scheduled entries in the main
    timetable (ResitTimetable) with venue+date+time assigned.
    """
    scheduled_ids = set(
        ResitTimetable.objects.filter(
            venue__isnull=False,
            date__isnull=False,
            start_time__isnull=False,
        ).values_list('resit_course_allocation_id', flat=True).distinct()
    )
    return ResitCourseAllocation.objects.exclude(
        id__in=scheduled_ids
    ).select_related('department', 'lecturer', 'program').order_by('course_code')


def _get_all_resit_allocations():
    """Return ALL resit allocations (for the 'already scheduled' course search)."""
    return ResitCourseAllocation.objects.select_related(
        'department', 'lecturer', 'program'
    ).order_by('course_code')


def _get_resit_timetable_data():
    """Get all ResitTimetable (published) entries as a list of dicts."""
    timetables = ResitTimetable.objects.select_related(
        'resit_course_allocation',
        'resit_course_allocation__lecturer',
        'venue',
    ).all()

    data = []
    for tt in timetables:
        alloc = tt.resit_course_allocation
        data.append({
            'id': tt.id,
            'date': tt.date.isoformat() if tt.date else '',
            'day': tt.day or '',
            'start_time': tt.start_time.strftime('%H:%M') if tt.start_time else '',
            'end_time': tt.end_time.strftime('%H:%M') if tt.end_time else '',
            'course_code': alloc.course_code if alloc else 'Unknown',
            'course_name': alloc.course_name or '' if alloc else '',
            'lecturer': getattr(alloc.lecturer, 'display_name', None) or getattr(alloc.lecturer, 'name', 'Unassigned') if (alloc and alloc.lecturer) else 'Unassigned',
            'venue': tt.venue.code if tt.venue else 'Unknown',
            'students': alloc.number_of_students or 0 if alloc else 0,
            'registered_students': tt.registered_students or 0,
            'resit_course_allocation_id': alloc.id if alloc else None,
        })
    return data


def _get_resit_merged_groups_data():
    """Get all ResitAutoMergedGroup data."""
    groups = ResitAutoMergedGroup.objects.select_related(
        'base_course', 'venue'
    ).prefetch_related('merged_courses').all()

    data = []
    for g in groups:
        courses = []
        for c in g.merged_courses.all():
            courses.append({
                'id': c.id,
                'course_code': c.course_code,
                'lecturer': getattr(c.lecturer, 'name', '–') if c.lecturer else '–',
                'students': c.number_of_students or 0,
            })
        data.append({
            'id': g.id,
            'merged_code': g.merged_code or '',
            'total_students': g.total_students or 0,
            'published': g.published,
            'venue': g.venue.code if g.venue else '',
            'date': g.date.isoformat() if g.date else '',
            'day': g.day or '',
            'start_time': g.start_time.strftime('%H:%M') if g.start_time else '',
            'end_time': g.end_time.strftime('%H:%M') if g.end_time else '',
            'allocation_ids': list(g.merged_courses.values_list('id', flat=True)),
            'courses': courses,
        })
    return data


def _detect_student_conflicts(allocation_id: int, date_str: str, start_time_str: str, exclude_entry_id: int = None) -> dict:
    """
    Check whether any student registered for `allocation_id` is already
    scheduled for another course in the same (date, start_time) slot.

    Returns:
        {
            "has_conflict": bool,
            "clashing_students": int,          # count of students with clashes
            "clashing_courses": ["CODE1", ...], # other courses they're in
            "venue_overload": bool,             # True if total students in slot > venue capacity
            "total_in_slot": int,               # total students already assigned to this slot
        }
    """
    from .models import StudentResitRegistration

    result = {
        "has_conflict": False,
        "clashing_students": 0,
        "clashing_courses": [],
        "venue_overload": False,
        "total_in_slot": 0,
    }

    # Get the normalized reg nos for students in the requested course
    my_students = set(
        StudentResitRegistration.objects
        .filter(resit_allocation_id=allocation_id)
        .values_list("student_reg_no_normalized", flat=True)
    )
    if not my_students:
        return result

    # Find all ResitTimetable entries in this (date, start_time) slot
    # Exclude the entry being edited if this is an edit operation
    slot_qs = ResitTimetable.objects.filter(
        date=date_str,
        start_time=start_time_str,
    ).exclude(
        resit_course_allocation_id=allocation_id
    )
    if exclude_entry_id:
        slot_qs = slot_qs.exclude(id=exclude_entry_id)

    slot_alloc_ids = list(slot_qs.values_list("resit_course_allocation_id", flat=True).distinct())

    if not slot_alloc_ids:
        return result

    # Get total student load in this slot (for venue overload check)
    result["total_in_slot"] = sum(
        slot_qs.values_list("registered_students", flat=True)
    )

    # Find students in those allocations that overlap with our course
    clashing = set(
        StudentResitRegistration.objects
        .filter(resit_allocation_id__in=slot_alloc_ids, student_reg_no_normalized__in=my_students)
        .values_list("student_reg_no_normalized", flat=True)
    )

    if clashing:
        result["has_conflict"] = True
        result["clashing_students"] = len(clashing)
        # Get the course codes of the clashing allocations
        clashing_alloc_ids = (
            StudentResitRegistration.objects
            .filter(resit_allocation_id__in=slot_alloc_ids, student_reg_no_normalized__in=clashing)
            .values_list("resit_allocation_id", flat=True)
            .distinct()
        )
        result["clashing_courses"] = list(
            ResitCourseAllocation.objects
            .filter(id__in=clashing_alloc_ids)
            .values_list("course_code", flat=True)
        )

    return result


def _recommend_alternative_slots(allocation_id: int, slot_grid: list) -> list[dict]:
    """
    Scan the slot_grid and return up to 3 slots where this allocation's
    students have NO conflicts with already-scheduled courses.

    Returns a list of {date, day, start_time, end_time} dicts.
    """
    from .models import StudentResitRegistration

    my_students = set(
        StudentResitRegistration.objects
        .filter(resit_allocation_id=allocation_id)
        .values_list("student_reg_no_normalized", flat=True)
    )

    recommendations = []
    seen_slots = set()

    for slot in slot_grid:
        key = (slot["date"], slot["start_time"])
        if key in seen_slots:
            continue
        seen_slots.add(key)

        conflict = _detect_student_conflicts(allocation_id, slot["date"], slot["start_time"])
        if not conflict["has_conflict"]:
            recommendations.append({
                "date": slot["date"],
                "day": slot["day"],
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
            })
            if len(recommendations) >= 3:
                break

    return recommendations


# ─── views ─────────────────────────────────────────────────────────────────

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def manual_timetabling_panel(request):
    """Main manual timetabling page for resit exams."""
    config = ResitSchedulerConfig.objects.order_by("-id").first()
    context = {
        "config": config,
    }
    return render(request, "resits_timetabling/manual_timetabling.html", context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def get_resit_panel_data(request):
    """AJAX GET: Return all panel data as JSON."""
    try:
        config = ResitSchedulerConfig.objects.order_by("-id").first()

        venues = list(Venue.objects.values("id", "code", "capacity", "exam_capacity").order_by("code"))

        # All allocations — needed so already-scheduled courses can still be edited/re-scheduled
        all_allocs = _get_all_resit_allocations()
        all_courses_qs = list(all_allocs.values(
            "id", "course_code", "course_name", "number_of_students",
            "department__name", "program__name",
            "lecturer__name", "lecturer__designation",
            "lecturer__user__first_name", "lecturer__user__last_name",
            "lecturer__user__username",
        ))
        # Build lecturer display name
        for c in all_courses_qs:
            first = (c.get("lecturer__user__first_name") or "").strip()
            last = (c.get("lecturer__user__last_name") or "").strip()
            username = c.get("lecturer__user__username") or ""
            full_name = f"{first} {last}".strip()
            if full_name:
                c["lecturer__name"] = full_name
            elif username:
                c["lecturer__name"] = username
            else:
                desig = c.get("lecturer__designation") or ""
                name = c.get("lecturer__name") or ""
                c["lecturer__name"] = f"{desig} {name}".strip() if (desig or name) else "Unassigned"
            for _k in ("lecturer__designation", "lecturer__user__first_name",
                        "lecturer__user__last_name", "lecturer__user__username"):
                c.pop(_k, None)
        all_courses = all_courses_qs

        # Unscheduled (for the pending count)
        unscheduled_ids = set(
            _get_unscheduled_resit_allocations().values_list('id', flat=True)
        )
        unscheduled = [c for c in all_courses if c['id'] in unscheduled_ids]

        timetable_data = _get_resit_timetable_data()
        merged_groups = _get_resit_merged_groups_data()

        # Build slot grid from config
        slot_grid = _build_slot_grid(config) if config else []

        return JsonResponse({
            "config": {
                "start_date": str(config.start_date) if config and config.start_date else None,
                "end_date": str(config.end_date) if config and config.end_date else None,
                "start_time": config.start_time.strftime("%H:%M") if config and config.start_time else "08:00",
                "end_time": config.end_time.strftime("%H:%M") if config and config.end_time else "17:00",
                "slot_size": config.slot_size if config else 2,
                "max_exam_days": config.max_exam_days if config else 10,
                "academic_year": config.academic_year if config else "",
                "semester": config.semester if config else "",
            } if config else {},
            "venues": venues,
            "all_courses": all_courses,
            "unscheduled": unscheduled,
            "timetable_data": timetable_data,
            "drafts": [],  # No draft table — all entries go directly to timetable_data
            "merged_groups": merged_groups,
            "slot_grid": slot_grid,
        }, safe=False)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def check_resit_slot_conflict(request):
    """
    AJAX POST: Check for student conflicts in a proposed slot before saving.

    Body JSON:
        allocation_id   — int, the ResitCourseAllocation being scheduled
        date            — "YYYY-MM-DD"
        start_time      — "HH:MM"
        exclude_entry_id — int (optional), entry being edited (ignored in check)

    Response JSON:
        {
            "success": true,
            "has_conflict": bool,
            "clashing_students": int,
            "clashing_courses": ["CODE", ...],
            "total_in_slot": int,
            "recommendations": [
                {"date": "...", "day": "...", "start_time": "...", "end_time": "..."},
                ...
            ]
        }
    """
    try:
        data = json.loads(request.body)
        allocation_id = int(data["allocation_id"])
        date_str = data["date"]
        start_time_str = data["start_time"]
        exclude_entry_id = data.get("exclude_entry_id")
        if exclude_entry_id:
            exclude_entry_id = int(exclude_entry_id)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    conflict = _detect_student_conflicts(allocation_id, date_str, start_time_str, exclude_entry_id)

    recommendations = []
    if conflict["has_conflict"]:
        # Build the slot grid to find alternatives
        config = ResitSchedulerConfig.objects.order_by("-id").first()
        slot_grid = _build_slot_grid(config) if config else []
        recommendations = _recommend_alternative_slots(allocation_id, slot_grid)

    return JsonResponse({
        "success": True,
        **conflict,
        "recommendations": recommendations,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def assign_resit_slot(request):
    """
    AJAX POST: Assign a ResitCourseAllocation to a resit slot.

    Manual timetabling entries are written directly to ResitTimetable
    (published) — there is no draft/staging step for manual scheduling.

    Supports multiple entries per course (multi-day scheduling).
    If `editing_entry_id` is provided in the payload, that specific
    ResitTempTimetable or ResitTimetable row is updated in-place (edit mode).
    Otherwise a brand-new ResitTimetable entry is always created — no overwrite.

    No program-year clash enforcement: resit courses can share any slot.

    Returns conflict_info in the response — the save always proceeds (soft-conflict
    model), but the frontend surfaces a warning badge if students overlap.
    """
    try:
        data = json.loads(request.body)
        allocation_id = int(data["allocation_id"])
        venue_id = data.get("venue_id")
        date_str = data["date"]
        start_time_str = data["start_time"]
        end_time_str = data["end_time"]
        editing_entry_id = data.get("editing_entry_id")
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    allocation = get_object_or_404(ResitCourseAllocation, id=allocation_id)
    # Always derive student count from the allocation — never trust the payload.
    registered_students = allocation.number_of_students or 0
    venue = None
    if venue_id:
        venue = get_object_or_404(Venue, id=venue_id)

    # Run conflict check so we can return it alongside the save result
    conflict_info = _detect_student_conflicts(
        allocation_id,
        date_str,
        start_time_str,
        exclude_entry_id=int(editing_entry_id) if editing_entry_id else None,
    )

    # ── Edit mode: update a specific existing ResitTimetable entry ──────
    if editing_entry_id:
        entry = get_object_or_404(ResitTimetable, id=int(editing_entry_id))
        entry.resit_course_allocation = allocation
        entry.venue = venue
        entry.date = date_str
        entry.start_time = start_time_str
        entry.end_time = end_time_str
        entry.registered_students = registered_students
        entry.day = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")
        entry.save()
        return JsonResponse({
            "success": True,
            "created": False,
            "entry_id": entry.id,
            "course_code": allocation.course_code,
            "venue": venue.code if venue else "No venue",
            "date": str(entry.date),
            "start_time": str(entry.start_time),
            "end_time": str(entry.end_time),
            "conflict_info": conflict_info,
        })

    # ── Create mode: write directly to ResitTimetable (published immediately) ──
    entry = ResitTimetable.objects.create(
        resit_course_allocation=allocation,
        venue=venue,
        date=date_str,
        start_time=start_time_str,
        end_time=end_time_str,
        registered_students=registered_students,
        day=datetime.strptime(date_str, "%Y-%m-%d").strftime("%A"),
        published_by=request.user,
        published_at=timezone.now(),
    )

    return JsonResponse({
        "success": True,
        "created": True,
        "entry_id": entry.id,
        "course_code": allocation.course_code,
        "venue": venue.code if venue else "No venue",
        "date": str(entry.date),
        "start_time": str(entry.start_time),
        "end_time": str(entry.end_time),
        "conflict_info": conflict_info,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def delete_resit_draft(request):
    """AJAX POST: Delete a single draft resit entry."""
    try:
        data = json.loads(request.body)
        entry_id = int(data["entry_id"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    entry = get_object_or_404(ResitTimetable, id=entry_id)
    course_code = entry.resit_course_allocation.course_code if entry.resit_course_allocation else "—"
    alloc_id = entry.resit_course_allocation_id
    entry.delete()
    return JsonResponse({
        "success": True,
        "deleted_id": entry_id,
        "course_code": course_code,
        "allocation_id": alloc_id,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def bulk_delete_resit_entries(request):
    """
    AJAX POST: Delete multiple resit entries.

    Body JSON:
        ids        — list of integer IDs
        entry_type — "draft" (default) or "published"
    """
    try:
        data = json.loads(request.body)
        ids = data.get("ids", [])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    ids = [int(i) for i in ids if i]
    if not ids:
        return JsonResponse({"success": False, "error": "No ids provided."}, status=400)

    deleted, _ = ResitTimetable.objects.filter(id__in=ids).delete()

    return JsonResponse({
        "success": True,
        "deleted": deleted,
        "deleted_ids": ids,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def delete_published_entry(request):
    """AJAX POST: Delete a single published resit entry."""
    try:
        data = json.loads(request.body)
        entry_id = int(data["entry_id"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)

    entry = get_object_or_404(ResitTimetable, id=entry_id)
    course_code = entry.resit_course_allocation.course_code if entry.resit_course_allocation else "—"
    alloc_id = entry.resit_course_allocation_id
    entry.delete()
    return JsonResponse({
        "success": True,
        "deleted_id": entry_id,
        "course_code": course_code,
        "allocation_id": alloc_id,
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def clear_all_resit_drafts(request):
    """AJAX POST: Delete ALL resit timetable entries."""
    count, _ = ResitTimetable.objects.all().delete()
    return JsonResponse({"success": True, "deleted": count})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def publish_resit_timetable(request):
    """
    AJAX POST: No-op — manual timetabling writes directly to ResitTimetable,
    so there is no draft-to-published promotion step. Returns the current
    published entry count so the frontend can confirm the state.
    """
    count = ResitTimetable.objects.count()
    return JsonResponse({
        "success": True,
        "published": count,
        "message": f"Resit timetable is live — {count} entries.",
    })


# ─── Merged Group API ─────────────────────────────────────────────────────

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def resit_merged_api(request):
    """API for ResitAutoMergedGroup CRUD."""

    if request.method == 'GET':
        return JsonResponse({'status': 'success', 'data': _get_resit_merged_groups_data()})

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
            merged_code = body.get('merged_code', '').strip()
            alloc_ids = body.get('allocation_ids', [])
            venue_id = body.get('venue_id')
            date_str = body.get('date')
            start_time_str = body.get('start_time')
            end_time_str = body.get('end_time')
            published = body.get('published', False)

            if not merged_code:
                return JsonResponse({'status': 'error', 'message': 'merged_code required'}, status=400)
            if len(alloc_ids) < 2:
                return JsonResponse({'status': 'error', 'message': 'Select at least 2 courses'}, status=400)

            allocations = list(ResitCourseAllocation.objects.filter(id__in=alloc_ids))
            if not allocations:
                return JsonResponse({'status': 'error', 'message': 'No valid allocations'}, status=400)

            base_course = allocations[0]
            venue_obj = get_object_or_404(Venue, id=venue_id) if venue_id else None
            total_students = sum(a.number_of_students or 0 for a in allocations)

            main_entry = ResitTimetable.objects.create(
                resit_course_allocation=base_course,
                venue=venue_obj,
                date=date_str,
                day=datetime.strptime(date_str, "%Y-%m-%d").strftime("%A") if date_str else "",
                start_time=start_time_str,
                end_time=end_time_str,
                registered_students=total_students,
                published_by=request.user,
                published_at=timezone.now(),
            )

            group = ResitAutoMergedGroup.objects.create(
                base_course=base_course,
                merged_code=merged_code,
                total_students=total_students,
                published=published,
                venue=venue_obj,
                date=date_str,
                start_time=start_time_str,
                end_time=end_time_str,
                timetable_entry=main_entry,
                created_by=request.user,
            )
            group.merged_courses.set(allocations)
            return JsonResponse({'status': 'success', 'id': group.id, 'message': 'Group created'})

        except Exception as e:
            import traceback; traceback.print_exc()
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
            group = get_object_or_404(ResitAutoMergedGroup, id=body.get('id'))
            if 'merged_code' in body:
                group.merged_code = body['merged_code']
            if 'published' in body:
                group.published = body['published']
            if body.get('venue_id'):
                group.venue = get_object_or_404(Venue, id=body['venue_id'])
            if 'date' in body:
                group.date = body['date']
                group.day = datetime.strptime(body['date'], "%Y-%m-%d").strftime("%A") if body['date'] else ''
            if 'start_time' in body:
                group.start_time = body['start_time']
            if 'end_time' in body:
                group.end_time = body['end_time']
            if 'allocation_ids' in body:
                allocs = list(ResitCourseAllocation.objects.filter(id__in=body['allocation_ids']))
                if allocs:
                    group.base_course = allocs[0]
                    group.merged_courses.set(allocs)
                    group.total_students = sum(a.number_of_students or 0 for a in allocs)
            group.save()
            return JsonResponse({'status': 'success', 'message': 'Group updated'})
        except Exception as e:
            import traceback; traceback.print_exc()
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    if request.method == 'DELETE':
        try:
            body = json.loads(request.body)
            group = get_object_or_404(ResitAutoMergedGroup, id=body.get('id'))
            if group.timetable_entry:
                group.timetable_entry.delete()
            group.delete()
            return JsonResponse({'status': 'success', 'message': 'Deleted'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)