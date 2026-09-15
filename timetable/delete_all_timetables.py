from core.rbac import allowed_roles, Role
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.http import HttpResponse
from django.db import transaction, IntegrityError
from datetime import time, datetime, date
from timetable.models import Timetable, TimetableArchive, AutoMergedExamGroup


def safe_serialize(obj):
    """Converts time/date/datetime objects into JSON-safe ISO strings."""
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    elif isinstance(obj, (list, tuple)):
        return [safe_serialize(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: safe_serialize(v) for k, v in obj.items()}
    return obj


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required("timetable.approve_timetable", raise_exception=True)
@transaction.atomic
def delete_all_timetables(request):
    """
    Prompts user for academic year & semester,
    then safely archives and deletes timetable entries.
    Prevents duplicate archive creation.
    """
    if request.method == "POST":
        academic_year = request.POST.get("academic_year")
        semester = request.POST.get("semester")

        if not academic_year or not semester:
            messages.error(request, "⚠️ Please provide both academic year and semester.")
            return redirect("delete_all_timetables")

        # Get timetable entries
        entries = list(Timetable.objects.values())
        if not entries:
            messages.warning(request, "⚠️ No timetable entries found to archive.")
            return redirect("timetable_panel")

        safe_entries = safe_serialize(entries)

        try:
            # Check if an archive already exists for that year/semester/type
            existing = TimetableArchive.objects.filter(
                timetable_type="MAIN",
                semester=semester,
                academic_year=academic_year,
            ).first()

            if existing:
                existing.data = safe_entries
                existing.archived_by = request.user
                existing.archived_at = timezone.now()
                existing.save(update_fields=["data", "archived_by", "archived_at"])
                msg = "🌀 Existing archive updated successfully."
            else:
                TimetableArchive.objects.create(
                    timetable_type="MAIN",
                    semester=semester,
                    academic_year=academic_year,
                    archived_by=request.user,
                    data=safe_entries,
                )
                msg = "✅ Timetable archived successfully."

            # Delete old timetable data
            count, _ = Timetable.objects.all().delete()
            AutoMergedExamGroup.objects.all().delete()

            messages.success(request, f"{msg} {count} timetable entries deleted.")
            return redirect("timetable_panel")

        except IntegrityError:
            # Catch and handle any database constraint violations
            messages.error(request, "❌ Database integrity error — please try again.")
            return redirect("timetable_panel")

        except Exception as e:
            messages.error(request, f"❌ Unexpected error: {str(e)}")
            return redirect("timetable_panel")

    # If GET → display prompt form
    return render(request, "timetable/delete_timetable_confirm.html")