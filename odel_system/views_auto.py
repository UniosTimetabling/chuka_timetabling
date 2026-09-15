from core.rbac import allowed_roles, Role
"""
odel_system/views_auto.py
==========================
HTTP layer for the ODEL auto-scheduler.

All scheduling logic lives in:
    timetable/algorithms/odel_autosheduler_algorithm.py

This file handles:
  • Rendering the auto-scheduling page
  • Receiving AJAX run / publish / config / status calls
  • Delegating to the engine and returning JSON responses
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db import transaction
import json
import datetime
import logging
import traceback

from odel_system.models import (
    ODELCourseAllocation,
    ODELTimetableConfig,
    ODELTempTimetable,
    ODELTimetable,
    ODELExamTempTimetable,
    ODELExamTimetable,
)
from room_management.models import Venue

# ── engine imports ────────────────────────────────────────────────────────────
from timetable.algorithms.odel_autosheduler_algorithm import (
    generate_class_time_slots,
    generate_exam_time_slots,
    check_conflicts,
    run_class_scheduler as _run_class_engine,
    run_exam_scheduler as _run_exam_engine,
)

scheduler_logger = logging.getLogger("scheduler")


# ---------------------------------------------------------------------------
# Panel view
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def auto_scheduling_page(request):
    """Render the auto-scheduling dashboard."""
    config = ODELTimetableConfig.objects.first() or ODELTimetableConfig.objects.create()

    total_allocations = ODELCourseAllocation.objects.filter(submitted_to_tt=True).count()

    class_drafts_list = (
        ODELTempTimetable.objects.select_related("course_allocation", "venue")
        .order_by("-created_at")[:20]
    )
    exam_drafts_list = (
        ODELExamTempTimetable.objects.select_related("course_allocation", "venue")
        .order_by("-created_at")[:20]
    )

    scheduled_class_ids = (
        set(ODELTempTimetable.objects.values_list("course_allocation_id", flat=True))
        | set(ODELTimetable.objects.values_list("course_allocation_id", flat=True))
    )
    scheduled_exam_ids = (
        set(ODELExamTempTimetable.objects.values_list("course_allocation_id", flat=True))
        | set(ODELExamTimetable.objects.values_list("course_allocation_id", flat=True))
    )

    start_date = config.start_date
    dates = []
    for i in range(30):
        d = start_date + datetime.timedelta(days=i)
        if d.weekday() < 5:
            dates.append({"date": d.strftime("%Y-%m-%d"), "day": d.strftime("%A")})

    context = {
        "config": config,
        "total_allocations": total_allocations,
        "class_drafts": class_drafts_list,
        "exam_drafts": exam_drafts_list,
        "class_drafts_count": ODELTempTimetable.objects.count(),
        "exam_drafts_count": ODELExamTempTimetable.objects.count(),
        "class_approved_count": ODELTimetable.objects.count(),
        "exam_approved_count": ODELExamTimetable.objects.count(),
        "scheduled_class_ids": list(scheduled_class_ids),
        "scheduled_exam_ids": list(scheduled_exam_ids),
        "dates": dates[:10],
        "can_publish": request.user.has_perm("odel_system.approve_odel_timetable"),
    }
    return render(request, "odel_system/auto_page.html", context)


# ---------------------------------------------------------------------------
# Run schedulers
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("odel_system.add_odeltemptimetable")
@require_http_methods(["POST"])
def run_class_scheduler(request):
    """AJAX — delegate to engine, return result JSON."""
    try:
        data = json.loads(request.body)
        mode = data.get("mode", "balanced")

        ODELTempTimetable.objects.all().delete()

        config = ODELTimetableConfig.objects.first()
        if not config:
            return JsonResponse({"success": False, "error": "No configuration found"})

        allocations = list(
            ODELCourseAllocation.objects.filter(submitted_to_tt=True)
            .select_related("program_course__program", "lecturer")
        )
        if not allocations:
            return JsonResponse({"success": False, "error": "No allocations available for scheduling"})

        venues = list(Venue.objects.filter(capacity__isnull=False).order_by("-capacity"))
        if not venues:
            return JsonResponse({"success": False, "error": "No venues available"})

        dates: list[datetime.date] = []
        current = config.start_date
        while len(dates) < 20:
            if current.weekday() < 5:
                dates.append(current)
            current += datetime.timedelta(days=1)

        time_slots = generate_class_time_slots(config, config.start_date)
        if not time_slots:
            return JsonResponse({"success": False, "error": "Invalid time slot configuration"})

        result = _run_class_engine(
            allocations=allocations,
            venues=venues,
            dates=dates,
            time_slots=time_slots,
            config=config,
            mode=mode,
            user=request.user,
        )

        return JsonResponse({
            "success": True,
            "message": f"Scheduled {result['scheduled']} class courses in draft",
            **result,
        })

    except Exception as exc:
        scheduler_logger.error(
            "ODEL class scheduler fatal error: %s\n%s", exc, traceback.format_exc(),
        )
        return JsonResponse({"success": False, "error": str(exc)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("odel_system.add_odelexamtemptimetable")
@require_http_methods(["POST"])
def run_exam_scheduler(request):
    """AJAX — delegate to engine, return result JSON."""
    try:
        data = json.loads(request.body)
        mode = data.get("mode", "balanced")

        ODELExamTempTimetable.objects.all().delete()

        config = ODELTimetableConfig.objects.first()
        if not config:
            return JsonResponse({"success": False, "error": "No configuration found"})

        allocations = list(
            ODELCourseAllocation.objects.filter(submitted_to_tt=True)
            .select_related("program_course__program", "lecturer")
        )
        if not allocations:
            return JsonResponse({"success": False, "error": "No allocations available for scheduling"})

        venues = list(Venue.objects.filter(capacity__isnull=False).order_by("-capacity"))
        if not venues:
            return JsonResponse({"success": False, "error": "No venues available"})

        exam_dates: list[datetime.date] = []
        current = config.start_date + datetime.timedelta(days=14)
        while len(exam_dates) < 10:
            if current.weekday() < 5:
                exam_dates.append(current)
            current += datetime.timedelta(days=1)

        time_slots = generate_exam_time_slots(config, config.start_date)
        if not time_slots:
            return JsonResponse({"success": False, "error": "Invalid time slot configuration"})

        result = _run_exam_engine(
            allocations=allocations,
            venues=venues,
            exam_dates=exam_dates,
            time_slots=time_slots,
            config=config,
            mode=mode,
        )

        return JsonResponse({
            "success": True,
            "message": f"Scheduled {result['scheduled']} exam courses in draft",
            **result,
        })

    except Exception as exc:
        scheduler_logger.error(
            "ODEL exam scheduler fatal error: %s\n%s", exc, traceback.format_exc(),
        )
        return JsonResponse({"success": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("odel_system.approve_odel_timetable")
@require_http_methods(["POST"])
def publish_class_timetable(request):
    """Promote class drafts to approved ODELTimetable rows."""
    try:
        with transaction.atomic():
            drafts = ODELTempTimetable.objects.select_related("course_allocation", "venue").all()
            if not drafts.exists():
                return JsonResponse({"success": False, "error": "No class draft entries to publish"})

            ODELTimetable.objects.all().delete()
            published = 0
            for draft in drafts:
                ODELTimetable.objects.create(
                    course_allocation=draft.course_allocation,
                    venue=draft.venue,
                    date=draft.date,
                    start_time=draft.start_time,
                    end_time=draft.end_time,
                    approved_by=request.user,
                    approved_at=timezone.now(),
                )
                published += 1
            drafts.delete()

        return JsonResponse({"success": True, "message": f"Published {published} class timetable entries"})
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("odel_system.approve_odel_exam")
@require_http_methods(["POST"])
def publish_exam_timetable(request):
    """Promote exam drafts to approved ODELExamTimetable rows."""
    try:
        with transaction.atomic():
            drafts = ODELExamTempTimetable.objects.select_related("course_allocation", "venue").all()
            if not drafts.exists():
                return JsonResponse({"success": False, "error": "No exam draft entries to publish"})

            ODELExamTimetable.objects.all().delete()
            published = 0
            for draft in drafts:
                ODELExamTimetable.objects.create(
                    course_allocation=draft.course_allocation,
                    venue=draft.venue,
                    date=draft.date,
                    start_time=draft.start_time,
                    end_time=draft.end_time,
                    approved_by=request.user,
                    approved_at=timezone.now(),
                )
                published += 1
            drafts.delete()

        return JsonResponse({"success": True, "message": f"Published {published} exam timetable entries"})
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# Config update
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def update_config(request):
    """AJAX — update ODELTimetableConfig."""
    try:
        data = json.loads(request.body)
        config = ODELTimetableConfig.objects.first() or ODELTimetableConfig()

        if data.get("start_date"):
            config.start_date = datetime.datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        if data.get("end_date"):
            config.end_date = datetime.datetime.strptime(data["end_date"], "%Y-%m-%d").date()
        if data.get("day_start_time"):
            config.day_start_time = datetime.datetime.strptime(data["day_start_time"], "%H:%M").time()
        if data.get("day_end_time"):
            config.day_end_time = datetime.datetime.strptime(data["day_end_time"], "%H:%M").time()
        if data.get("class_slot_size"):
            config.class_slot_size = int(data["class_slot_size"])
        if data.get("exam_slot_size"):
            config.exam_slot_size = int(data["exam_slot_size"])
        if data.get("exam_break_duration"):
            config.exam_break_duration = int(data["exam_break_duration"])
        config.save()

        class_slots = generate_class_time_slots(config, config.start_date)
        exam_slots = generate_exam_time_slots(config, config.start_date)

        return JsonResponse({
            "success": True,
            "message": "Configuration updated successfully",
            "config": {
                "start_date": config.start_date.strftime("%Y-%m-%d"),
                "end_date": config.end_date.strftime("%Y-%m-%d"),
                "day_start_time": config.day_start_time.strftime("%H:%M"),
                "day_end_time": config.day_end_time.strftime("%H:%M"),
                "class_slot_size": config.class_slot_size,
                "exam_slot_size": config.exam_slot_size,
                "exam_break_duration": config.exam_break_duration,
            },
            "class_slots_preview": [s["display"] for s in class_slots[:3]],
            "exam_slots_preview": [s["display"] for s in exam_slots[:3]],
        })
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_scheduler_status(request):
    """AJAX — return current counts and recent drafts."""
    total = ODELCourseAllocation.objects.filter(submitted_to_tt=True).count()

    recent_class_drafts = []
    for draft in (
        ODELTempTimetable.objects.select_related("course_allocation", "venue")
        .order_by("-created_at")[:10]
    ):
        recent_class_drafts.append({
            "id": draft.id,
            "date": draft.date.strftime("%Y-%m-%d"),
            "start_time": draft.start_time.strftime("%H:%M"),
            "end_time": draft.end_time.strftime("%H:%M"),
            "course_allocation__course_code": draft.course_allocation.course_code,
            "venue__code": draft.venue.code,
            "students": draft.course_allocation.number_of_students,
        })

    recent_exam_drafts = []
    for draft in (
        ODELExamTempTimetable.objects.select_related("course_allocation", "venue")
        .order_by("-created_at")[:10]
    ):
        recent_exam_drafts.append({
            "id": draft.id,
            "date": draft.date.strftime("%Y-%m-%d"),
            "start_time": draft.start_time.strftime("%H:%M"),
            "end_time": draft.end_time.strftime("%H:%M"),
            "course_allocation__course_code": draft.course_allocation.course_code,
            "venue__code": draft.venue.code,
            "students": draft.course_allocation.number_of_students,
        })

    return JsonResponse({
        "total_allocations": total,
        "class_drafts": ODELTempTimetable.objects.count(),
        "exam_drafts": ODELExamTempTimetable.objects.count(),
        "class_approved": ODELTimetable.objects.count(),
        "exam_approved": ODELExamTimetable.objects.count(),
        "recent_class_drafts": recent_class_drafts,
        "recent_exam_drafts": recent_exam_drafts,
    })