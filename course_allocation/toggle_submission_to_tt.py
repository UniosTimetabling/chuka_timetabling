# -----------------------
# Submission Control
# -----------------------
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from django.shortcuts import get_object_or_404
from department_management.models import Department
from course_allocation.models import SubmissionControl
@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def toggle_submission_to_tt(request):
    """
    Toggle whether DVC-approved allocations can be forwarded to timetable (scoped to department).
    """
    detected_dept = Department.objects.filter(leader=request.user).first()
    if not detected_dept:
        return JsonResponse({"status": "error", "msg": "Not a department leader."}, status=403)

    control, _ = SubmissionControl.objects.get_or_create(department=detected_dept)
    control.allow_submission_to_tt = not control.allow_submission_to_tt
    control.save()

    return JsonResponse({
        "status": "success",
        "allow_submission_to_tt": control.allow_submission_to_tt,
        "department": detected_dept.name
    })
