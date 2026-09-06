# -----------------------
# Submission Control
# -----------------------
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.utils import timezone
from core.rbac import allowed_roles, Role
from django.shortcuts import get_object_or_404
from department_management.models import Department
from course_allocation.models import SubmissionControl, DVCActionLog
from notifications.models import Notification

# Canonical DVC group names (kept in sync with notifications.notification_apis.DVC_GROUPS)
DVC_GROUP_NAMES = ["dvc", "dvc_admins"]


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def toggle_submission_to_dvc(request):
    """
    Toggle whether CODs can submit allocations to DVC (scoped to department).

    When this flips from OPEN (True) -> LOCKED (False), that is the COD's
    "submit for DVC review" action. On that transition we:
      1. Log a DVCActionLog(action="submit") event with a timestamp.
      2. Notify the DVC group that a new submission has arrived, including
         department name, submitter, and time.
      3. Notify the COD themself with a confirmation that their submission
         went through.
    """
    detected_dept = Department.objects.filter(leader=request.user).first()
    if not detected_dept:
        return JsonResponse({"status": "error", "msg": "Not a department leader."}, status=403)

    control, _ = SubmissionControl.objects.get_or_create(department=detected_dept)
    was_open = control.allow_submission_to_dvc
    control.allow_submission_to_dvc = not control.allow_submission_to_dvc
    control.save()

    just_submitted = was_open and not control.allow_submission_to_dvc

    if just_submitted:
        now_display = timezone.localtime().strftime("%b %d, %Y — %H:%M")
        submitter_name = request.user.get_full_name() or request.user.username

        # Log the discrete event (used for audit + future reporting).
        DVCActionLog.objects.create(
            department=detected_dept,
            action=DVCActionLog.ACTION_SUBMIT,
            actor=request.user,
            reported=True,  # submit events notify immediately, nothing to batch
        )

        # Notify every DVC / DVC Admin.
        dvc_groups = Group.objects.filter(name__in=DVC_GROUP_NAMES)
        for group in dvc_groups:
            Notification.create_for_group(
                f"📥 {detected_dept.name} ({submitter_name}) submitted their course "
                f"allocation for review at {now_display}.",
                group,
            )

        # Confirm to the COD that their submission was received.
        Notification.create_for_user(
            f"✅ Your course allocation submission for {detected_dept.name} was "
            f"received at {now_display} and is now awaiting DVC review.",
            request.user,
        )

    return JsonResponse({
        "status": "success",
        "allow_submission_to_dvc": control.allow_submission_to_dvc,
        "department": detected_dept.name,
        "submitted": just_submitted,
    })
