from core.rbac import allowed_roles, Role
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.db.models import Q, Count, Sum
from django.utils import timezone
from datetime import datetime, timedelta
import json
import logging

from .models import (
    Campus, 
    CampusCourseAllocation, 
    CampusTimetable, 
    CampusTempTimetable,
    CampusExamTimetable, 
    CampusExamTempTimetable,
    CampusSchedulerConfig,
    CampusExamSchedulerConfig
)
from program_management.models import Program
from lecturer_portal.models import Lecturer
from department_management.models import Department

logger = logging.getLogger(__name__)


def is_timetable_admin(user):
    """Check if user has timetable admin permissions"""
    from core.rbac import user_has_role
    return user.is_superuser or user_has_role(
        user, Role.DIRECTOR, Role.TIMETABLE_ADMIN
    )


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@user_passes_test(is_timetable_admin)
def manual_timetable(request):
    """
    Main manual timetable creation view
    """
    # Get or create scheduler config
    config, _ = CampusSchedulerConfig.objects.get_or_create(
        defaults={
            'start_time': datetime.strptime('08:00', '%H:%M').time(),
            'end_time': datetime.strptime('18:00', '%H:%M').time(),
            'slot_size': 2
        }
    )
    
    # Get or create exam scheduler config
    exam_config, _ = CampusExamSchedulerConfig.objects.get_or_create(
        defaults={
            'start_date': timezone.now().date(),
            'start_time': datetime.strptime('09:00', '%H:%M').time(),
            'end_time': datetime.strptime('17:00', '%H:%M').time(),
            'slot_size': 3,
            'max_exam_days': 14
        }
    )
    
    # Get available allocations (approved and not rejected)
    available_allocations = CampusCourseAllocation.objects.filter(
        approved_by_dvc=True,
        rejected_by_dvc=False
    ).select_related('program', 'lecturer', 'campus').order_by('course_code')
    
    # Get venues (you'll need to create a Venue model or use existing)
    # For now, using a placeholder - you should replace with your actual Venue model
    venues = []  # Replace with Venue.objects.all() if you have a Venue model
    
    # Get existing timetables
    class_timetable = CampusTimetable.objects.select_related(
        'course_allocation', 'campus'
    ).order_by('day', 'start_time')
    
    exam_timetable = CampusExamTimetable.objects.select_related(
        'course_allocation', 'campus'
    ).order_by('date', 'start_time')
    
    context = {
        'config': {
            'start_date': exam_config.start_date,
            'end_date': exam_config.start_date + timedelta(days=exam_config.max_exam_days - 1),
            'day_start_time': config.start_time,
            'day_end_time': config.end_time,
            'class_slot_size': config.slot_size,
            'exam_slot_size': exam_config.slot_size,
            'exam_break_duration': 60,  # Default 60 minutes
        },
        'available_allocations': available_allocations,
        'venues': venues,
        'class_timetable': class_timetable,
        'exam_timetable': exam_timetable,
        'can_clear_timetable': is_timetable_admin(request.user),
    }
    
    return render(request, 'campus/manual_timetable.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["GET"])
def get_time_slots(request):
    """
    Get available time slots for a given date and type
    """
    date_str = request.GET.get('date')
    entry_type = request.GET.get('type', 'class')
    
    if not date_str:
        return JsonResponse({'error': 'Date is required'}, status=400)
    
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Invalid date format'}, status=400)
    
    # Get config based on type
    if entry_type == 'class':
        config = CampusSchedulerConfig.objects.first()
        if not config:
            return JsonResponse({'error': 'Scheduler configuration not found'}, status=404)
        
        start_time = config.start_time
        end_time = config.end_time
        slot_duration = config.slot_size
    else:
        config = CampusExamSchedulerConfig.objects.first()
        if not config:
            return JsonResponse({'error': 'Exam scheduler configuration not found'}, status=404)
        
        # Check if date is within exam period
        exam_end_date = config.start_date + timedelta(days=config.max_exam_days - 1)
        if selected_date < config.start_date or selected_date > exam_end_date:
            return JsonResponse({
                'error': f'Date must be between {config.start_date} and {exam_end_date}'
            }, status=400)
        
        # Check excluded days
        excluded_dates = config.excluded_date_list()
        if date_str in excluded_dates:
            return JsonResponse({'error': 'This date is excluded from scheduling'}, status=400)
        
        start_time = config.start_time
        end_time = config.end_time
        slot_duration = config.slot_size
    
    # Generate time slots
    slots = []
    current = datetime.combine(selected_date, start_time)
    end_datetime = datetime.combine(selected_date, end_time)
    
    while current + timedelta(hours=slot_duration) <= end_datetime:
        slot_end = current + timedelta(hours=slot_duration)
        slots.append({
            'start': current.strftime('%H:%M'),
            'end': slot_end.strftime('%H:%M'),
            'display': f"{current.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}"
        })
        current = slot_end
    
    return JsonResponse({'slots': slots})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["GET"])
def get_timetable_data(request):
    """
    Get timetable data for display
    """
    entry_type = request.GET.get('type', 'class')
    
    if entry_type == 'class':
        entries = CampusTimetable.objects.select_related(
            'course_allocation__program',
            'course_allocation__lecturer',
            'campus'
        ).all().order_by('day', 'start_time')
        
        entries_data = []
        for entry in entries:
            entries_data.append({
                'id': entry.id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'date': entry.day,  # For class, day is string (Monday, Tuesday)
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'lecturer': entry.course_allocation.lecturer.display_name if entry.course_allocation.lecturer else 'Unassigned',
                'students': entry.course_allocation.number_of_students,
                'campus': entry.campus.code if entry.campus else 'N/A',
            })
    else:
        entries = CampusExamTimetable.objects.select_related(
            'course_allocation__program',
            'course_allocation__lecturer',
            'campus'
        ).all().order_by('date', 'start_time')
        
        entries_data = []
        for entry in entries:
            entries_data.append({
                'id': entry.id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'date': entry.date.strftime('%Y-%m-%d'),
                'day': entry.day,
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'lecturer': entry.course_allocation.lecturer.display_name if entry.course_allocation.lecturer else 'Unassigned',
                'students': entry.course_allocation.number_of_students,
                'campus': entry.campus.code if entry.campus else 'N/A',
            })
    
    return JsonResponse({
        'success': True,
        'entries': entries_data
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["GET"])
def check_availability(request):
    """
    Check if a time slot is available for scheduling
    """
    allocation_id = request.GET.get('allocation_id')
    venue_id = request.GET.get('venue_id')  # You'll need to implement venue checking
    date = request.GET.get('date')
    start_time = request.GET.get('start_time')
    end_time = request.GET.get('end_time')
    exam_mode = request.GET.get('exam_mode') == 'true'
    
    conflicts = []
    
    # Check course allocation conflicts
    if allocation_id:
        allocation = get_object_or_404(CampusCourseAllocation, id=allocation_id)
        
        if exam_mode:
            # Check if course already scheduled for exam on this date
            existing = CampusExamTimetable.objects.filter(
                course_allocation=allocation,
                date=date
            ).exists()
            if existing:
                conflicts.append(f"This course already has an exam scheduled on {date}")
        else:
            # For class, check if course already scheduled on this day
            day_name = datetime.strptime(date, '%Y-%m-%d').strftime('%A')
            existing = CampusTimetable.objects.filter(
                course_allocation=allocation,
                day=day_name,
                start_time=start_time
            ).exists()
            if existing:
                conflicts.append(f"This course already has a class scheduled on {day_name} at {start_time}")
    
    # Check lecturer availability
    if allocation_id:
        allocation = CampusCourseAllocation.objects.get(id=allocation_id)
        if allocation.lecturer:
            if exam_mode:
                lecturer_conflicts = CampusExamTimetable.objects.filter(
                    course_allocation__lecturer=allocation.lecturer,
                    date=date,
                    start_time=start_time
                ).exists()
                if lecturer_conflicts:
                    conflicts.append(f"Lecturer {allocation.lecturer.display_name} already has an exam at this time")
            else:
                day_name = datetime.strptime(date, '%Y-%m-%d').strftime('%A')
                lecturer_conflicts = CampusTimetable.objects.filter(
                    course_allocation__lecturer=allocation.lecturer,
                    day=day_name,
                    start_time=start_time
                ).exists()
                if lecturer_conflicts:
                    conflicts.append(f"Lecturer {allocation.lecturer.display_name} already has a class at this time")
    
    # Check campus conflicts (if campuses manage their own venues, skip venue check)
    # You can add campus-level conflict checking here if needed
    
    return JsonResponse({
        'available': len(conflicts) == 0,
        'conflicts': conflicts
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def create_class_entry(request):
    """
    Create a new class timetable entry
    """
    try:
        data = json.loads(request.body)
        
        allocation_id = data.get('course_allocation')
        date = data.get('date')
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        
        # Validate required fields
        if not all([allocation_id, date, start_time, end_time]):
            return JsonResponse({
                'success': False,
                'error': 'Missing required fields'
            }, status=400)
        
        # Get allocation
        allocation = get_object_or_404(CampusCourseAllocation, id=allocation_id)
        
        # Convert date to day name
        day_name = datetime.strptime(date, '%Y-%m-%d').strftime('%A')
        
        # Check for conflicts
        conflict = CampusTimetable.objects.filter(
            course_allocation=allocation,
            day=day_name,
            start_time=start_time
        ).exists()
        
        if conflict:
            return JsonResponse({
                'success': False,
                'error': 'This course already has a class scheduled at this time'
            }, status=400)
        
        # Create entry
        entry = CampusTimetable.objects.create(
            course_allocation=allocation,
            day=day_name,
            start_time=start_time,
            end_time=end_time,
            campus=allocation.campus  # Use allocation's campus
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Class scheduled successfully for {allocation.course_code}',
            'entry_id': entry.id
        })
        
    except Exception as e:
        logger.error(f"Error creating class entry: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def create_exam_entry(request):
    """
    Create a new exam timetable entry
    """
    try:
        data = json.loads(request.body)
        
        allocation_id = data.get('course_allocation')
        date = data.get('date')
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        
        # Validate required fields
        if not all([allocation_id, date, start_time, end_time]):
            return JsonResponse({
                'success': False,
                'error': 'Missing required fields'
            }, status=400)
        
        # Get allocation
        allocation = get_object_or_404(CampusCourseAllocation, id=allocation_id)
        
        # Convert date to day name
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        day_name = date_obj.strftime('%A')
        
        # Check for conflicts
        conflict = CampusExamTimetable.objects.filter(
            course_allocation=allocation,
            date=date
        ).exists()
        
        if conflict:
            return JsonResponse({
                'success': False,
                'error': f'This course already has an exam scheduled on {date}'
            }, status=400)
        
        # Check exam period
        exam_config = CampusExamSchedulerConfig.objects.first()
        if exam_config:
            exam_end_date = exam_config.start_date + timedelta(days=exam_config.max_exam_days - 1)
            if date_obj.date() < exam_config.start_date or date_obj.date() > exam_end_date:
                return JsonResponse({
                    'success': False,
                    'error': f'Date must be within exam period ({exam_config.start_date} to {exam_end_date})'
                }, status=400)
        
        # Create entry
        entry = CampusExamTimetable.objects.create(
            course_allocation=allocation,
            day=day_name,
            date=date,
            start_time=start_time,
            end_time=end_time,
            campus=allocation.campus  # Use allocation's campus
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Exam scheduled successfully for {allocation.course_code}',
            'entry_id': entry.id
        })
        
    except Exception as e:
        logger.error(f"Error creating exam entry: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def delete_class_entry(request, entry_id):
    """
    Delete a class timetable entry
    """
    try:
        entry = get_object_or_404(CampusTimetable, id=entry_id)
        course_code = entry.course_allocation.course_code
        entry.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Class entry for {course_code} deleted successfully'
        })
        
    except Exception as e:
        logger.error(f"Error deleting class entry: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def delete_exam_entry(request, entry_id):
    """
    Delete an exam timetable entry
    """
    try:
        entry = get_object_or_404(CampusExamTimetable, id=entry_id)
        course_code = entry.course_allocation.course_code
        entry.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Exam entry for {course_code} deleted successfully'
        })
        
    except Exception as e:
        logger.error(f"Error deleting exam entry: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def clear_timetable(request):
    """
    Clear timetable entries
    """
    try:
        data = json.loads(request.body)
        target = data.get('target', 'class')
        
        if target == 'class' or target == 'both':
            count = CampusTimetable.objects.all().delete()[0]
            if target == 'class':
                return JsonResponse({
                    'success': True,
                    'message': f'{count} class entries cleared'
                })
        
        if target == 'exam' or target == 'both':
            count = CampusExamTimetable.objects.all().delete()[0]
            if target == 'exam':
                return JsonResponse({
                    'success': True,
                    'message': f'{count} exam entries cleared'
                })
        
        if target == 'both':
            return JsonResponse({
                'success': True,
                'message': 'All timetable entries cleared'
            })
        
        return JsonResponse({
            'success': False,
            'error': 'Invalid target'
        }, status=400)
        
    except Exception as e:
        logger.error(f"Error clearing timetable: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def update_config(request):
    """
    Update scheduler configuration
    """
    try:
        data = json.loads(request.body)
        
        # Update class config
        class_config = CampusSchedulerConfig.objects.first()
        if class_config:
            class_config.start_time = datetime.strptime(data.get('day_start_time', '08:00'), '%H:%M').time()
            class_config.end_time = datetime.strptime(data.get('day_end_time', '18:00'), '%H:%M').time()
            class_config.slot_size = int(data.get('class_slot_size', 2))
            class_config.save()
        
        # Update exam config
        exam_config = CampusExamSchedulerConfig.objects.first()
        if exam_config:
            exam_config.start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
            exam_config.start_time = datetime.strptime(data.get('day_start_time', '09:00'), '%H:%M').time()
            exam_config.end_time = datetime.strptime(data.get('day_end_time', '17:00'), '%H:%M').time()
            exam_config.slot_size = int(data.get('exam_slot_size', 3))
            exam_config.max_exam_days = 14  # You can make this configurable
            exam_config.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Configuration updated successfully',
            'config': {
                'start_date': exam_config.start_date.strftime('%Y-%m-%d'),
                'end_date': (exam_config.start_date + timedelta(days=exam_config.max_exam_days - 1)).strftime('%Y-%m-%d')
            }
        })
        
    except Exception as e:
        logger.error(f"Error updating config: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)