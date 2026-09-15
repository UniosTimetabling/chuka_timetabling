from core.rbac import allowed_roles, Role
"""
campuses_timetable/automatic_views.py
=======================================
HTTP layer for the Campus auto-scheduler.

All scheduling logic lives in:
    timetable/algorithms/campus_autosheduler_algorithm.py

This file handles:
  • Rendering the scheduler panel (auto_scheduler view)
  • Receiving AJAX calls and delegating to the engine
  • Publishing / clearing drafts
  • Config updates
  • Status endpoint
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from datetime import datetime, timedelta
import json
import logging

from .models import (
    CampusCourseAllocation,
    CampusTimetable,
    CampusTempTimetable,
    CampusExamTimetable,
    CampusExamTempTimetable,
    CampusSchedulerConfig,
    CampusExamSchedulerConfig,
    CampusTimetableArchive,
)

# ── engine import ─────────────────────────────────────────────────────────────
from timetable.algorithms.campus_autosheduler_algorithm import (
    ClassScheduler,
    ExamScheduler,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permission helper
# ---------------------------------------------------------------------------

def is_timetable_admin(user):
    from core.rbac import user_has_role
    return user.is_superuser or user_has_role(
        user, Role.DIRECTOR, Role.TIMETABLE_ADMIN
    )


# ---------------------------------------------------------------------------
# Panel view
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@user_passes_test(is_timetable_admin)
def auto_scheduler(request):
    """Render the auto-scheduler dashboard."""
    class_config, _ = CampusSchedulerConfig.objects.get_or_create(
        defaults={
            "start_time": datetime.strptime("08:00", "%H:%M").time(),
            "end_time": datetime.strptime("18:00", "%H:%M").time(),
            "slot_size": 2,
        }
    )
    exam_config, _ = CampusExamSchedulerConfig.objects.get_or_create(
        defaults={
            "start_date": timezone.now().date(),
            "start_time": datetime.strptime("09:00", "%H:%M").time(),
            "end_time": datetime.strptime("17:00", "%H:%M").time(),
            "slot_size": 3,
            "max_exam_days": 14,
        }
    )

    approved_count = CampusCourseAllocation.objects.filter(
        approved_by_dvc=True, rejected_by_dvc=False
    ).count()

    class_drafts = CampusTempTimetable.objects.select_related(
        "course_allocation__program",
        "course_allocation__lecturer",
        "campus",
    ).order_by("day", "start_time")

    exam_drafts = CampusExamTempTimetable.objects.select_related(
        "course_allocation__program",
        "course_allocation__lecturer",
        "campus",
    ).order_by("date", "start_time")

    context = {
        "config": {
            "start_date": exam_config.start_date,
            "end_date": exam_config.start_date + timedelta(days=exam_config.max_exam_days - 1),
            "day_start_time": class_config.start_time,
            "day_end_time": class_config.end_time,
            "class_slot_size": class_config.slot_size,
            "exam_slot_size": exam_config.slot_size,
            "exam_break_duration": 60,
        },
        "class_drafts": class_drafts,
        "exam_drafts": exam_drafts,
        "class_drafts_count": class_drafts.count(),
        "exam_drafts_count": exam_drafts.count(),
        "approved_count": approved_count,
        "can_publish": is_timetable_admin(request.user),
    }
    return render(request, "campus/auto_scheduler.html", context)


# ---------------------------------------------------------------------------
# Run schedulers
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def run_class_scheduler(request):
    """AJAX — run class scheduler engine and return result."""
    try:
        data = json.loads(request.body)
        mode = data.get("mode", "balanced")
        logger.info("run_class_scheduler: mode=%s", mode)

        approved_count = CampusCourseAllocation.objects.filter(
            approved_by_dvc=True, rejected_by_dvc=False
        ).count()
        if approved_count == 0:
            return JsonResponse({
                "success": False,
                "error": "No approved allocations found. Please approve some courses first.",
            }, status=400)

        scheduler = ClassScheduler(mode=mode)
        success = scheduler.run()

        if success:
            return JsonResponse({
                "success": True,
                "message": f"Class scheduler completed. Scheduled {len(scheduler.scheduled)} courses.",
                "conflict_list": scheduler.conflicts,
                "scheduled_count": len(scheduler.scheduled),
            })
        return JsonResponse({
            "success": False,
            "error": "Scheduler failed. " + (" ".join(scheduler.conflicts) if scheduler.conflicts else "No slots available."),
            "conflict_list": scheduler.conflicts,
        }, status=400)

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as exc:
        logger.exception("run_class_scheduler error")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def run_exam_scheduler(request):
    """AJAX — run exam scheduler engine and return result."""
    try:
        data = json.loads(request.body)
        mode = data.get("mode", "balanced")
        logger.info("run_exam_scheduler: mode=%s", mode)

        approved_count = CampusCourseAllocation.objects.filter(
            approved_by_dvc=True, rejected_by_dvc=False
        ).count()
        if approved_count == 0:
            return JsonResponse({
                "success": False,
                "error": "No approved allocations found. Please approve some courses first.",
            }, status=400)

        exam_config = CampusExamSchedulerConfig.objects.first()
        if not exam_config:
            return JsonResponse({
                "success": False,
                "error": "Exam scheduler configuration not found. Please configure exam settings first.",
            }, status=400)

        scheduler = ExamScheduler(mode=mode)
        success = scheduler.run()

        if success:
            return JsonResponse({
                "success": True,
                "message": f"Exam scheduler completed. Scheduled {len(scheduler.scheduled)} courses.",
                "conflict_list": scheduler.conflicts,
                "scheduled_count": len(scheduler.scheduled),
            })
        return JsonResponse({
            "success": False,
            "error": "Scheduler failed. " + (" ".join(scheduler.conflicts) if scheduler.conflicts else "No slots available."),
            "conflict_list": scheduler.conflicts,
        }, status=400)

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as exc:
        logger.exception("run_exam_scheduler error")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def publish_class_timetable(request):
    """Promote class drafts to permanent CampusTimetable rows."""
    try:
        temp_entries = CampusTempTimetable.objects.select_related("course_allocation").all()
        if not temp_entries.exists():
            return JsonResponse({"success": False, "error": "No class drafts to publish"}, status=400)

        CampusTimetable.objects.all().delete()
        created = 0
        for temp in temp_entries:
            CampusTimetable.objects.create(
                course_allocation=temp.course_allocation,
                day=temp.day,
                start_time=temp.start_time,
                end_time=temp.end_time,
                campus=temp.campus,
            )
            created += 1

        CampusTimetableArchive.objects.create(
            timetable_type="MAIN",
            semester="1",
            academic_year=f"{timezone.now().year}/{timezone.now().year + 1}",
            archived_by=request.user,
            data={
                "entries": list(CampusTimetable.objects.values()),
                "published_by": request.user.username,
                "published_at": timezone.now().isoformat(),
            },
            campus=None,
        )
        logger.info("Published %d class entries", created)
        return JsonResponse({"success": True, "message": f"Class timetable published with {created} entries"})

    except Exception as exc:
        logger.exception("publish_class_timetable error")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def publish_exam_timetable(request):
    """Promote exam drafts to permanent CampusExamTimetable rows."""
    try:
        temp_entries = CampusExamTempTimetable.objects.select_related("course_allocation").all()
        if not temp_entries.exists():
            return JsonResponse({"success": False, "error": "No exam drafts to publish"}, status=400)

        CampusExamTimetable.objects.all().delete()
        created = 0
        for temp in temp_entries:
            CampusExamTimetable.objects.create(
                course_allocation=temp.course_allocation,
                day=temp.day,
                date=temp.date,
                start_time=temp.start_time,
                end_time=temp.end_time,
                campus=temp.campus,
            )
            created += 1

        CampusTimetableArchive.objects.create(
            timetable_type="EXAM",
            semester="1",
            academic_year=f"{timezone.now().year}/{timezone.now().year + 1}",
            archived_by=request.user,
            data={
                "entries": list(CampusExamTimetable.objects.values()),
                "published_by": request.user.username,
                "published_at": timezone.now().isoformat(),
            },
            campus=None,
        )
        logger.info("Published %d exam entries", created)
        return JsonResponse({"success": True, "message": f"Exam timetable published with {created} entries"})

    except Exception as exc:
        logger.exception("publish_exam_timetable error")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Clear drafts
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def clear_timetable_drafts(request):
    """Clear class and/or exam drafts."""
    try:
        data = json.loads(request.body)
        target = data.get("target", "class")

        if target == "class":
            count = CampusTempTimetable.objects.all().delete()[0]
            return JsonResponse({"success": True, "message": f"{count} class drafts cleared"})
        elif target == "exam":
            count = CampusExamTempTimetable.objects.all().delete()[0]
            return JsonResponse({"success": True, "message": f"{count} exam drafts cleared"})
        else:
            cc = CampusTempTimetable.objects.all().delete()[0]
            ec = CampusExamTempTimetable.objects.all().delete()[0]
            return JsonResponse({"success": True, "message": f"Cleared {cc} class and {ec} exam drafts"})

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as exc:
        logger.exception("clear_timetable_drafts error")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Config update
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def update_scheduler_config(request):
    """Update class and exam scheduler configuration."""
    try:
        data = json.loads(request.body)

        class_config = CampusSchedulerConfig.objects.first() or CampusSchedulerConfig()
        class_config.start_time = datetime.strptime(data.get("day_start_time", "08:00"), "%H:%M").time()
        class_config.end_time = datetime.strptime(data.get("day_end_time", "18:00"), "%H:%M").time()
        class_config.slot_size = int(data.get("class_slot_size", 2))
        class_config.save()

        exam_config = CampusExamSchedulerConfig.objects.first() or CampusExamSchedulerConfig()
        exam_config.start_date = datetime.strptime(data.get("start_date"), "%Y-%m-%d").date()
        exam_config.start_time = datetime.strptime(data.get("day_start_time", "09:00"), "%H:%M").time()
        exam_config.end_time = datetime.strptime(data.get("day_end_time", "17:00"), "%H:%M").time()
        exam_config.slot_size = int(data.get("exam_slot_size", 3))
        end_date = datetime.strptime(data.get("end_date"), "%Y-%m-%d").date()
        exam_config.max_exam_days = max((end_date - exam_config.start_date).days + 1, 1)
        exam_config.save()

        logger.info("Scheduler config updated")
        return JsonResponse({"success": True, "message": "Configuration updated successfully"})

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as exc:
        logger.exception("update_scheduler_config error")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def scheduler_status(request):
    """Return current draft / permanent counts for the dashboard."""
    try:
        return JsonResponse({
            "success": True,
            "status": {
                "class_drafts": CampusTempTimetable.objects.count(),
                "exam_drafts": CampusExamTempTimetable.objects.count(),
                "class_permanent": CampusTimetable.objects.count(),
                "exam_permanent": CampusExamTimetable.objects.count(),
                "approved_allocations": CampusCourseAllocation.objects.filter(
                    approved_by_dvc=True, rejected_by_dvc=False
                ).count(),
            },
        })
    except Exception as exc:
        logger.exception("scheduler_status error")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)