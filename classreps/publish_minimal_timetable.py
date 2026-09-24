from django.shortcuts import redirect, get_object_or_404
from django.db import transaction
from django.contrib import messages

from core.rbac import classrep_required
from .models import ClassRep, MinimalTimetable
from timetable.models import Timetable


@classrep_required
@transaction.atomic
def publish_minimal_timetable(request):
    """Copy the main timetable for this rep's program into the MinimalTimetable."""
    rep_id = request.session.get("classrep_id")
    rep = get_object_or_404(ClassRep, id=rep_id)

    timetable_entries = Timetable.objects.filter(course_allocation__program=rep.program)

    # Replace any existing minimal entries for this rep
    MinimalTimetable.objects.filter(class_rep=rep).delete()

    for t in timetable_entries:
        MinimalTimetable.objects.create(
            class_rep=rep,
            course_code=t.course_allocation.course_code,
            course_name=t.course_allocation.course_name,
            day=t.day,
            start_time=t.start_time,
            end_time=t.end_time,
            venue=t.venue,
        )

    messages.success(request, "Timetable published successfully to Minimal Timetable.")
    return redirect("classrep_dashboard")
