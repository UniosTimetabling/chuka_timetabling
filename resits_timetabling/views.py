"""
resits_timetabling/views.py
Top-level view dispatcher - only contains views that don't belong to other modules.

This file should ONLY contain:
  - toggle_resit_submission (simple view that doesn't fit elsewhere)
  - Configuration management views (new)
  
All other views are imported from:
  - cod_panel.py (COD panel views)
  - manual_timetabler.py (manual timetabling views)
  - resit_autosheduler.py (auto-scheduler views)
"""
import json
from datetime import datetime, timedelta
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required
from core.rbac import allowed_roles, Role
from core.group_required import group_required

from .models import ResitSubmissionControl, ResitSchedulerConfig


__all__ = [
    "toggle_resit_submission",
    "get_resit_config",
    "save_resit_config",
    "get_excluded_days_calendar",
    "toggle_excluded_day",
]


# Helper function to get user's department
def _get_user_department(user):
    """Get the department associated with a user."""
    if not user or not user.is_authenticated:
        return None
    
    # Method 1: Check if user is Department leader
    try:
        from department_management.models import Department
        dept = Department.objects.filter(leader=user).first()
        if dept:
            return dept
    except Exception:
        pass
    
    # Method 2: Check Lecturer profile
    try:
        from lecturer_portal.models import Lecturer
        lect = Lecturer.objects.filter(user=user).first()
        if lect and lect.department:
            return lect.department
    except Exception:
        pass
    
    # Method 3: Check by email match
    try:
        from lecturer_portal.models import Lecturer
        if user.email:
            lect = Lecturer.objects.filter(email__iexact=user.email).first()
            if lect and lect.department:
                return lect.department
    except Exception:
        pass
    
    return None


@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
@require_GET
def toggle_resit_submission(request):
    """
    Toggle the resit submission control for the user's department.
    This controls whether departments can submit resit allocations for timetabling.
    """
    department = _get_user_department(request.user)
    
    if not department:
        return JsonResponse({
            "status": "error", 
            "message": "No department associated with your account. Please contact administrator."
        }, status=400)
    
    try:
        control, created = ResitSubmissionControl.objects.get_or_create(department=department)
        control.allow_submission_to_timetabling = not control.allow_submission_to_timetabling
        control.updated_by = request.user
        control.save()
        
        return JsonResponse({
            "status": "success",
            "allow_submission_to_timetabling": control.allow_submission_to_timetabling,
            "message": f"Resit submission {'enabled' if control.allow_submission_to_timetabling else 'disabled'} for {department.name}"
        })
    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": f"Error toggling submission control: {str(e)}"
        }, status=500)


# ============================================================================
# RESIT SCHEDULER CONFIGURATION VIEWS
# ============================================================================

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def get_resit_config(request):
    """Get the latest ResitSchedulerConfig as JSON."""
    try:
        config = ResitSchedulerConfig.objects.order_by("-id").first()
        if config:
            return JsonResponse({
                "success": True,
                "config": {
                    "id": config.id,
                    "academic_year": config.academic_year,
                    "semester": config.semester,
                    "start_date": config.start_date.isoformat() if config.start_date else None,
                    "end_date": config.end_date.isoformat() if config.end_date else None,
                    "start_time": config.start_time.strftime("%H:%M") if config.start_time else "08:00",
                    "end_time": config.end_time.strftime("%H:%M") if config.end_time else "17:00",
                    "slot_size": config.slot_size,
                    "max_exam_days": config.max_exam_days,
                    # spacing_ratio repurposed as break_between_slots (hours)
                    "break_between_slots": int(config.spacing_ratio) if config.spacing_ratio >= 1 else 0,
                    "excluded_days": config.excluded_days,
                }
            })
        else:
            return JsonResponse({
                "success": True,
                "config": None
            })
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def save_resit_config(request):
    """Save or update the ResitSchedulerConfig and automatically generate slots."""
    try:
        data = json.loads(request.body)
        
        # Get existing config or create new one
        config = ResitSchedulerConfig.objects.order_by("-id").first()
        if not config:
            config = ResitSchedulerConfig()
        
        # Update fields
        config.academic_year = data.get("academic_year", "")
        config.semester = data.get("semester", "")
        config.start_date = data.get("start_date")
        config.start_time = data.get("start_time", "08:00")
        config.end_time = data.get("end_time", "17:00")
        config.slot_size = int(data.get("slot_size", 2))
        config.max_exam_days = int(data.get("max_exam_days", 10))
        # spacing_ratio stores break_between_slots in hours (>= 1).
        config.spacing_ratio = float(data.get("break_between_slots", 0))

        # Always reset end_date so model.save() recalculates it fresh,
        # then _generate_slots() rebuilds ResitTimeSlot rows from scratch.
        config.end_date = None

        # Validate required fields
        if not config.start_date:
            return JsonResponse({
                "success": False,
                "error": "Start date is required"
            }, status=400)

        # Save — model.save() recalculates excluded_days, end_date, and calls
        # _generate_slots() to write the ResitTimeSlot rows to the DB.
        config.save()

        # Report how many slots were actually stored in the DB.
        from .models import ResitTimeSlot
        slot_count = ResitTimeSlot.objects.filter(config=config).count()
        date_range = config.get_date_range()

        return JsonResponse({
            "success": True,
            "message": f"Configuration saved. {len(date_range)} exam days, {slot_count} time slots generated.",
            "config_id": config.id,
            "exam_days": len(date_range),
            "slot_count": slot_count,
            "dates": [d[0] for d in date_range],
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_GET
def get_excluded_days_calendar(request):
    """Get a calendar of dates with excluded status for a given start date."""
    try:
        start_date_str = request.GET.get("start_date")
        if not start_date_str:
            return JsonResponse({
                "success": False,
                "error": "start_date parameter required"
            }, status=400)
        
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        
        # Get config to know excluded days
        config = ResitSchedulerConfig.objects.order_by("-id").first()
        excluded_set = set()
        if config and config.excluded_days:
            excluded_set = set(config.excluded_date_list())
        
        # Generate dates for 60 days ahead
        days = []
        for i in range(60):
            date = start_date + timedelta(days=i)
            date_str = date.isoformat()
            is_weekend = date.weekday() >= 5
            
            # Determine if excluded (either explicitly excluded or weekend)
            is_excluded = (date_str in excluded_set) or is_weekend
            
            days.append({
                "date": date_str,
                "day_name": date.strftime("%A"),
                "weekend": is_weekend,
                "excluded": is_excluded
            })
        
        return JsonResponse({
            "success": True,
            "days": days
        })
        
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_POST
def toggle_excluded_day(request):
    """Toggle a specific date in the excluded_days list and regenerate slots."""
    try:
        data = json.loads(request.body)
        date = data.get("date")
        excluded = data.get("excluded", True)
        
        if not date:
            return JsonResponse({
                "success": False,
                "error": "Date required"
            }, status=400)
        
        # Get config
        config = ResitSchedulerConfig.objects.order_by("-id").first()
        if not config:
            # Create default config if none exists
            config = ResitSchedulerConfig()
            config.save()
        
        # Get current excluded list
        current_excluded = config.excluded_date_list()
        
        if excluded:
            if date not in current_excluded:
                current_excluded.append(date)
        else:
            if date in current_excluded:
                current_excluded.remove(date)
        
        # FIX: Reset end_date so model.save() recalculates the date range
        # and _generate_slots() rebuilds ResitTimeSlot rows after the
        # excluded-days list changes.
        config.end_date = None
        config.excluded_days = ", ".join(sorted(current_excluded))
        config.save()

        from .models import ResitTimeSlot
        slot_count = ResitTimeSlot.objects.filter(config=config).count()
        date_range = config.get_date_range()

        return JsonResponse({
            "success": True,
            "message": f"Date {date} {'excluded' if excluded else 'included'} successfully. {len(date_range)} exam days, {slot_count} slots available.",
            "excluded_days": config.excluded_days,
            "exam_days": len(date_range),
            "slot_count": slot_count,
        })
        
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)