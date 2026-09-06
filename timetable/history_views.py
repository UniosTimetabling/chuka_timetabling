from core.rbac import allowed_roles, Role
from datetime import timedelta
from django.utils.timezone import now
from django.http import JsonResponse, HttpResponseForbidden
from django.core.paginator import Paginator
from django.db import transaction
from django.apps import apps
from core.group_required import group_required  # adjust if needed
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.views.decorators.http import require_POST
from timetable.models import Timetable, ExamTimetable, TimetableArchive
import json


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("timetable.approve_timetable", raise_exception=True)
def archive_timetable_view(request):
    """
    Combined archive and archive panel view.
    """
    archives = TimetableArchive.objects.all()
    grouped = {
        "MAIN": archives.filter(timetable_type="MAIN"),
        "EXAM": archives.filter(timetable_type="EXAM"),
    }

    if request.method == "POST":
        semester = request.POST.get("semester")
        academic_year = request.POST.get("academic_year")
        timetable_type = request.POST.get("timetable_type")

        if not (semester and academic_year and timetable_type):
            messages.error(request, "⚠️ Please fill all required fields.")
            return redirect("archive_timetable")

        if timetable_type == "EXAM":
            entries = list(ExamTimetable.objects.values())
        else:
            entries = list(Timetable.objects.values())

        if not entries:
            messages.warning(request, "⚠️ No timetable entries found to archive.")
            return redirect("archive_timetable")

        existing = TimetableArchive.objects.filter(
            semester=semester, academic_year=academic_year, timetable_type=timetable_type
        ).first()

        if existing:
            messages.error(request, "⚠️ Archive for this semester already exists.")
            return redirect("archive_timetable")

        TimetableArchive.objects.create(
            timetable_type=timetable_type,
            semester=semester,
            academic_year=academic_year,
            archived_by=request.user,
            data=entries,
        )

        if timetable_type == "EXAM":
            ExamTimetable.objects.all().delete()
        else:
            Timetable.objects.all().delete()

        TimetableArchive.clean_old_archives()
        messages.success(request, "✅ Timetable archived successfully!")
        return redirect("archive_timetable")

    return render(request, "timetable/archive_combined.html", {"grouped": grouped})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("timetable.approve_timetable", raise_exception=True)
@require_POST
def unarchive_timetable_view(request, archive_id):
    """
    Restore archived timetable entries to live database.
    """
    archive = get_object_or_404(TimetableArchive, id=archive_id)
    entries = archive.data

    if archive.timetable_type == "EXAM":
        ExamTimetable.objects.bulk_create([ExamTimetable(**e) for e in entries])
    else:
        Timetable.objects.bulk_create([Timetable(**e) for e in entries])

    archive.delete()
    messages.success(request, "✅ Timetable unarchived and published successfully.")
    return redirect("archive_timetable")
