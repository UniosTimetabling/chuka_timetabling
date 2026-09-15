from django.views.decorators.http import require_POST
from timetable.models import MergedCourseGroup
from course_allocation.models import CourseAllocation
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from core.group_required import group_required

@require_POST
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def add_to_merged(request):
    group_id = request.POST.get("group_id")
    course_id = request.POST.get("course_id")
    try:
        mg = MergedCourseGroup.objects.get(id=group_id)
        course = CourseAllocation.objects.get(id=course_id)
        mg.merged_courses.add(course)
        mg.total_students = sum(c.number_of_students for c in mg.merged_courses.all())
        mg.save()
        return JsonResponse({"status": "success"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})
