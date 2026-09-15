from core.rbac import allowed_roles, Role
# ---------- DELETE ALL ----------
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.http import HttpResponse
from django.db import transaction, IntegrityError
from datetime import time, datetime, date
from timetable.models import ExamTimetable, TimetableArchive, MergedCourseGroup, ExamTempTimetable, MergedCourseGroupTimetable


def safe_serialize(obj):
    """Convert date/time/datetime to JSON-safe strings."""
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    elif isinstance(obj, (list, tuple)):
        return [safe_serialize(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: safe_serialize(v) for k, v in obj.items()}
    return obj


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("timetable.approve_timetable", raise_exception=True)
def delete_all_Exam_timetables(request):
    """
    Prompts user for academic year & semester, then archives and deletes all exam timetable entries.
    Handles IntegrityError safely to avoid transaction failures.
    """
    if request.method == "POST":
        academic_year = request.POST.get("academic_year")
        semester = request.POST.get("semester")

        if not academic_year or not semester:
            messages.error(request, "⚠️ Please provide both academic year and semester.")
            return redirect("delete_all_Exam_timetables")

        entries = list(ExamTimetable.objects.values())
        if not entries:
            messages.warning(request, "⚠️ No exam timetable entries found to archive.")
            return redirect("exam_timetable_panel")

        safe_entries = safe_serialize(entries)

        try:
            with transaction.atomic():
                # Try to create an archive entry
                TimetableArchive.objects.create(
                    timetable_type="EXAM",
                    semester=semester,
                    academic_year=academic_year,
                    archived_by=request.user,
                    data=safe_entries,
                )

                # Delete timetable + all related records
                count, _ = ExamTimetable.objects.all().delete()
                ExamTempTimetable.objects.all().delete()
                MergedCourseGroupTimetable.objects.all().delete()
                MergedCourseGroup.objects.all().delete()
                from timetable.models import SharedVenueExamGroup
                SharedVenueExamGroup.objects.all().delete()

        except IntegrityError:
            # If archive already exists, skip creating it but still allow deletion
            messages.warning(
                request,
                f"⚠️ Archive for {academic_year} - {semester} (EXAM) already exists. Old one preserved."
            )
            count, _ = ExamTimetable.objects.all().delete()
            ExamTempTimetable.objects.all().delete()
            MergedCourseGroupTimetable.objects.all().delete()
            MergedCourseGroup.objects.all().delete()

        messages.success(request, f"✅ Archived and deleted {count} exam timetable entries successfully.")
        return redirect("exam_timetable_panel")

    # GET → Render prompt form
    return render(request, "timetable/delete_exam_timetable_confirm.html")