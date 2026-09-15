from django.http import JsonResponse
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
