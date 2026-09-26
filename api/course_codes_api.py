from django.http import JsonResponse
from django.db.models import Count, Max
from program_management.models import ProgramCourse

def api_course_codes(request):
    """
    Returns JSON list of course codes (and names) filtered by program if provided.
    """
    program_id = request.GET.get("program_id")
    if program_id:
        courses = ProgramCourse.objects.filter(program_id=program_id)
    else:
        courses = ProgramCourse.objects.all()

    data = [
        {"code": c.course_code, "name": c.course_name}
        for c in courses.order_by("course_code")
    ]
    return JsonResponse({"courses": data})


def api_course_codes_signature(request):
    """
    Cheap "has anything changed?" check for the course-code list, so the
    frontend can skip re-fetching/re-rendering the full course list on every
    focus and only do so when the underlying data actually changed.

    Instead of returning all rows, this runs a single aggregate query
    (COUNT + MAX(updated_at) + MAX(id)) over the same filtered queryset
    api_course_codes() would use, and returns a small "sig" string. The
    client caches the last sig it saw per program and only re-fetches the
    full list when the sig differs (or on first load / program change).
    """
    program_id = request.GET.get("program_id")
    if program_id:
        courses = ProgramCourse.objects.filter(program_id=program_id)
    else:
        courses = ProgramCourse.objects.all()

    agg = courses.aggregate(
        count=Count("id"),
        max_updated=Max("updated_at"),
        max_id=Max("id"),
    )

    count = agg["count"] or 0
    max_id = agg["max_id"] or 0
    max_updated = agg["max_updated"].isoformat() if agg["max_updated"] else ""
    sig = f"{count}:{max_id}:{max_updated}"

    return JsonResponse({"sig": sig})
