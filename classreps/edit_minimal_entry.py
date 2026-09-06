from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from core.rbac import classrep_required
from .models import ClassRep, MinimalTimetable


@classrep_required
def edit_minimal_entry(request, entry_id):
    rep_id = request.session.get("classrep_id")
    # Scope the lookup to this rep so a rep can't edit another rep's entry
    entry = get_object_or_404(MinimalTimetable, id=entry_id, class_rep_id=rep_id)

    if request.method == "POST":
        entry.day        = request.POST.get("day")
        entry.start_time = request.POST.get("start_time")
        entry.end_time   = request.POST.get("end_time")
        entry.venue      = request.POST.get("venue")
        entry.save()
        messages.success(request, "Entry updated successfully.")
        return redirect("classrep_dashboard")

    return render(request, "classrep_edit_entry.html", {"entry": entry})
