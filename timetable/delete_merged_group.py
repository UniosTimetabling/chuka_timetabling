from django.views.decorators.http import require_POST
from timetable.models import MergedCourseGroup
from course_allocation.models import CourseAllocation
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from core.group_required import group_required

@require_POST
@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def delete_merged_group(request):
    group_id = request.POST.get("group_id")
    try:
        mg = MergedCourseGroup.objects.get(id=group_id)
        mg.delete()
        return JsonResponse({"status": "success"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})