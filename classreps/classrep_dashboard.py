from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.db import transaction
from django.views.decorators.http import require_POST

from core.rbac import classrep_required
from .models import ClassRep, MinimalTimetable
from timetable.models import Timetable
from program_management.models import Program, ProgramCode


@classrep_required
@transaction.atomic
def classrep_dashboard(request):
    rep_id = request.session.get("classrep_id")
    rep = get_object_or_404(ClassRep, id=rep_id)

    notifications = rep.notifications.all()
    timetable = Timetable.objects.filter(course_allocation__program=rep.program)
    minimal = MinimalTimetable.objects.filter(class_rep=rep)
    programs = Program.objects.all()

    # Handle AJAX submission for adding program codes
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        program_id = request.POST.get("program_id")
        code = (request.POST.get("program_code") or "").strip().upper()

        if not (program_id and code):
            return HttpResponseBadRequest("Missing program or code")

        program = get_object_or_404(Program, id=program_id)
        program_code, created = ProgramCode.objects.get_or_create(program=program, code=code)

        return JsonResponse({
            "success": True,
            "created": created,
            "program": program.name,
            "code": program_code.code,
            "message": "✅ Code added successfully!" if created else "⚠️ This code already exists for this program.",
        })

    # Normal GET rendering
    programscode = ProgramCode.objects.select_related("program").all().order_by("program__name")

    table_data = []
    for prog in programs:
        codes = list(prog.program_codes.values_list("code", flat=True))
        table_data.append({
            "program": prog.name,
            "codes": ", ".join(codes) if codes else "—",
        })

    context = {
        "rep": rep,
        "notifications": notifications,
        "timetable": timetable,
        "minimal": minimal,
        "programs": programs,
        "programs_codes": table_data,
    }

    return render(request, "classrep_dashboard.html", context)
