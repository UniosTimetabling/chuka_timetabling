from core.rbac import allowed_roles, Role
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db import transaction
import json
import datetime

from odel_system.models import (
    ODELCourseAllocation, ODELTimetable, ODELExamTimetable, ODELTimetableConfig
)
from room_management.models import Venue


def generate_class_time_slots(config, date):
    """
    Generate class time slots based on config day_start_time, day_end_time, and class_slot_size
    No breaks between class slots
    """
    if not config:
        return []
    
    # Convert times to datetime for calculation
    start_dt = datetime.datetime.combine(date, config.day_start_time)
    end_dt = datetime.datetime.combine(date, config.day_end_time)
    
    # Calculate total minutes and slot duration
    total_minutes = int((end_dt - start_dt).total_seconds() / 60)
    slot_duration = total_minutes // config.class_slot_size
    
    slots = []
    current = start_dt
    
    for i in range(config.class_slot_size):
        slot_end = current + datetime.timedelta(minutes=slot_duration)
        if slot_end <= end_dt:
            slots.append({
                'start': current.time(),
                'end': slot_end.time(),
                'display': f"{current.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}",
                'slot_number': i + 1
            })
            current = slot_end
    
    return slots


def generate_exam_time_slots(config, date):
    """
    Generate exam time slots based on config day_start_time, day_end_time, and exam_slot_size
    Includes breaks between exam slots
    """
    if not config:
        return []
    
    # Convert times to datetime for calculation
    start_dt = datetime.datetime.combine(date, config.day_start_time)
    end_dt = datetime.datetime.combine(date, config.day_end_time)
    
    # Calculate total available minutes
    total_minutes = int((end_dt - start_dt).total_seconds() / 60)
    
    # Calculate exam duration and break duration
    total_exam_minutes = total_minutes - (config.exam_break_duration * (config.exam_slot_size - 1))
    exam_duration = total_exam_minutes // config.exam_slot_size
    
    slots = []
    current = start_dt
    
    for i in range(config.exam_slot_size):
        slot_end = current + datetime.timedelta(minutes=exam_duration)
        if slot_end <= end_dt:
            slots.append({
                'start': current.time(),
                'end': slot_end.time(),
                'display': f"{current.strftime('%H:%M')} - {slot_end.strftime('%H:%M')}",
                'slot_number': i + 1
            })
            
            # Add break after exam (except after last slot)
            if i < config.exam_slot_size - 1:
                current = slot_end + datetime.timedelta(minutes=config.exam_break_duration)
            else:
                current = slot_end
    
    return slots


def check_conflicts(allocation, venue, date, start_time, end_time, exam_mode=False, exclude_id=None):
    """
    Comprehensive conflict checking for creating/updating entries:
    1. Venue double booking
    2. Lecturer teaching two courses at same time
    3. Same course scheduled twice (unless exam mode)
    4. Program having two classes at same time
    """
    conflicts = []
    
    # Get the course allocation object
    try:
        alloc_obj = ODELCourseAllocation.objects.get(id=allocation)
    except ODELCourseAllocation.DoesNotExist:
        conflicts.append("Course allocation not found")
        return conflicts
    
    # Check if allocation is submitted to timetable
    if not alloc_obj.submitted_to_tt:
        conflicts.append("This course allocation has not been submitted to timetabling")
        return conflicts
    
    # Base queryset filters
    base_filters = {
        'date': date,
        'start_time__lt': end_time,
        'end_time__gt': start_time
    }
    
    if exclude_id:
        base_filters['id__ne'] = exclude_id
    
    # 1. Check venue double booking
    if exam_mode:
        venue_conflict = ODELExamTimetable.objects.filter(
            venue_id=venue, **base_filters
        ).exists()
    else:
        venue_conflict = ODELTimetable.objects.filter(
            venue_id=venue, **base_filters
        ).exists()
    
    if venue_conflict:
        conflicts.append("Venue is already booked at this time")
    
    # 2. Check lecturer conflict
    if alloc_obj.lecturer:
        if exam_mode:
            lecturer_conflict = ODELExamTimetable.objects.filter(
                course_allocation__lecturer=alloc_obj.lecturer,
                **base_filters
            ).exists()
        else:
            lecturer_conflict = ODELTimetable.objects.filter(
                course_allocation__lecturer=alloc_obj.lecturer,
                **base_filters
            ).exists()
        
        if lecturer_conflict:
            conflicts.append("Lecturer is already teaching another course at this time")
    
    # 3. Check same course conflict (unless in exam mode)
    if not exam_mode:
        if ODELTimetable.objects.filter(
            course_allocation_id=allocation, **base_filters
        ).exists():
            conflicts.append("This course is already scheduled at this time")
    
    # 4. Check program conflict
    if alloc_obj.program_course and alloc_obj.program_course.program:
        program = alloc_obj.program_course.program
        program_courses = ODELCourseAllocation.objects.filter(
            program_course__program=program
        ).values_list('id', flat=True)
        
        if exam_mode:
            program_conflict = ODELExamTimetable.objects.filter(
                course_allocation_id__in=program_courses,
                **base_filters
            ).exists()
        else:
            program_conflict = ODELTimetable.objects.filter(
                course_allocation_id__in=program_courses,
                **base_filters
            ).exists()
        
        if program_conflict:
            conflicts.append("Program already has another class scheduled at this time")
    
    return conflicts


def validate_date_in_range(date, config):
    """Check if date is within configured range"""
    if not config:
        return False, "No configuration found"
    
    if date < config.start_date or date > config.end_date:
        return False, f"Date must be between {config.start_date} and {config.end_date}"
    
    return True, None


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def manual_scheduling_page(request):
    """
    Single page for all manual scheduling (both class and exam)
    """
    config = ODELTimetableConfig.objects.first()
    if not config:
        config = ODELTimetableConfig.objects.create()
    
    # Get all allocations that are submitted to tt
    available_allocations = ODELCourseAllocation.objects.filter(
        submitted_to_tt=True
    ).select_related(
        'program_course__program', 
        'lecturer'
    )
    
    # Exclude already scheduled courses
    scheduled_class_ids = ODELTimetable.objects.values_list('course_allocation_id', flat=True)
    scheduled_exam_ids = ODELExamTimetable.objects.values_list('course_allocation_id', flat=True)
    scheduled_ids = list(scheduled_class_ids) + list(scheduled_exam_ids)
    available_allocations = available_allocations.exclude(id__in=scheduled_ids)
    
    # Get venues
    venues = Venue.objects.filter(capacity__isnull=False).order_by('code')
    
    # Get class and exam timetables
    class_timetable = ODELTimetable.objects.select_related(
        'course_allocation__program_course__program',
        'course_allocation__lecturer',
        'venue',
        'approved_by'
    ).all().order_by('date', 'start_time')
    
    exam_timetable = ODELExamTimetable.objects.select_related(
        'course_allocation__program_course__program',
        'course_allocation__lecturer',
        'venue',
        'approved_by'
    ).all().order_by('date', 'start_time')
    
    # Get program information for allocations
    for alloc in available_allocations:
        if alloc.program_course and alloc.program_course.program:
            alloc.program_info = str(alloc.program_course.program)
        else:
            alloc.program_info = 'N/A'
    
    context = {
        'config': config,
        'available_allocations': available_allocations,
        'venues': venues,
        'class_timetable': class_timetable,
        'exam_timetable': exam_timetable,
        'date_range': {
            'start': config.start_date.strftime('%Y-%m-%d'),
            'end': config.end_date.strftime('%Y-%m-%d')
        },
        'can_approve_class': request.user.has_perm('odel_system.approve_odel_timetable'),
        'can_approve_exam': request.user.has_perm('odel_system.approve_odel_exam'),
        'can_clear_timetable': request.user.has_perm('odel_system.clear_timetable'),
    }
    return render(request, 'odel_system/manual_page.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def create_class_entry(request):
    """AJAX handler for creating class timetable entry (saves directly to main timetable)"""
    try:
        data = json.loads(request.body)
        
        # Validate date is within range
        config = ODELTimetableConfig.objects.first()
        date_obj = datetime.datetime.strptime(data['date'], '%Y-%m-%d').date()
        is_valid, error_msg = validate_date_in_range(date_obj, config)
        
        if not is_valid:
            return JsonResponse({'success': False, 'error': error_msg})
        
        # Comprehensive conflict checking
        conflicts = check_conflicts(
            allocation=data['course_allocation'],
            venue=data['venue'],
            date=data['date'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            exam_mode=False
        )
        
        if conflicts:
            return JsonResponse({
                'success': False, 
                'error': ' | '.join(conflicts)
            })
        
        # Create entry directly in main timetable (no draft)
        entry = ODELTimetable.objects.create(
            course_allocation_id=data['course_allocation'],
            venue_id=data['venue'],
            date=data['date'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            approved_by=request.user,
            approved_at=timezone.now()
        )
        
        # Get related data for response
        entry = ODELTimetable.objects.select_related(
            'course_allocation__program_course__program',
            'course_allocation__lecturer',
            'venue'
        ).get(id=entry.id)
        
        # Get program info
        program_info = 'N/A'
        if entry.course_allocation.program_course and entry.course_allocation.program_course.program:
            program_info = str(entry.course_allocation.program_course.program)
        
        return JsonResponse({
            'success': True,
            'message': 'Class timetable entry created successfully',
            'entry': {
                'id': entry.id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'lecturer': str(entry.course_allocation.lecturer) if entry.course_allocation.lecturer else 'Unassigned',
                'program': program_info,
                'venue': entry.venue.code,
                'venue_id': entry.venue.id,
                'date': entry.date.strftime('%Y-%m-%d'),
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'students': entry.course_allocation.number_of_students,
                'approved_by': str(entry.approved_by) if entry.approved_by else '',
                'approved_at': entry.approved_at.strftime('%Y-%m-%d %H:%M') if entry.approved_at else ''
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def create_exam_entry(request):
    """AJAX handler for creating exam timetable entry (saves directly to main exam timetable)"""
    try:
        data = json.loads(request.body)
        
        # Validate date is within range
        config = ODELTimetableConfig.objects.first()
        date_obj = datetime.datetime.strptime(data['date'], '%Y-%m-%d').date()
        is_valid, error_msg = validate_date_in_range(date_obj, config)
        
        if not is_valid:
            return JsonResponse({'success': False, 'error': error_msg})
        
        # Comprehensive conflict checking
        conflicts = check_conflicts(
            allocation=data['course_allocation'],
            venue=data['venue'],
            date=data['date'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            exam_mode=True
        )
        
        if conflicts:
            return JsonResponse({
                'success': False, 
                'error': ' | '.join(conflicts)
            })
        
        # Create entry directly in main exam timetable (no draft)
        entry = ODELExamTimetable.objects.create(
            course_allocation_id=data['course_allocation'],
            venue_id=data['venue'],
            date=data['date'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            approved_by=request.user,
            approved_at=timezone.now()
        )
        
        # Get related data for response
        entry = ODELExamTimetable.objects.select_related(
            'course_allocation__program_course__program',
            'course_allocation__lecturer',
            'venue'
        ).get(id=entry.id)
        
        # Get program info
        program_info = 'N/A'
        if entry.course_allocation.program_course and entry.course_allocation.program_course.program:
            program_info = str(entry.course_allocation.program_course.program)
        
        return JsonResponse({
            'success': True,
            'message': 'Exam timetable entry created successfully',
            'entry': {
                'id': entry.id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'lecturer': str(entry.course_allocation.lecturer) if entry.course_allocation.lecturer else 'Unassigned',
                'program': program_info,
                'venue': entry.venue.code,
                'venue_id': entry.venue.id,
                'date': entry.date.strftime('%Y-%m-%d'),
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'students': entry.course_allocation.number_of_students,
                'approved_by': str(entry.approved_by) if entry.approved_by else '',
                'approved_at': entry.approved_at.strftime('%Y-%m-%d %H:%M') if entry.approved_at else ''
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def update_class_entry(request, pk):
    """AJAX handler for updating class timetable entry"""
    try:
        entry = get_object_or_404(ODELTimetable, pk=pk)
        data = json.loads(request.body)
        
        # Validate date is within range
        config = ODELTimetableConfig.objects.first()
        date_obj = datetime.datetime.strptime(data['date'], '%Y-%m-%d').date()
        is_valid, error_msg = validate_date_in_range(date_obj, config)
        
        if not is_valid:
            return JsonResponse({'success': False, 'error': error_msg})
        
        # Comprehensive conflict checking
        conflicts = check_conflicts(
            allocation=data['course_allocation'],
            venue=data['venue'],
            date=data['date'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            exam_mode=False,
            exclude_id=pk
        )
        
        if conflicts:
            return JsonResponse({
                'success': False, 
                'error': ' | '.join(conflicts)
            })
        
        # Update entry
        entry.course_allocation_id = data['course_allocation']
        entry.venue_id = data['venue']
        entry.date = data['date']
        entry.start_time = data['start_time']
        entry.end_time = data['end_time']
        entry.save()
        
        # Get updated data
        entry.refresh_from_db()
        entry = ODELTimetable.objects.select_related(
            'course_allocation__program_course__program',
            'course_allocation__lecturer',
            'venue'
        ).get(id=entry.id)
        
        # Get program info
        program_info = 'N/A'
        if entry.course_allocation.program_course and entry.course_allocation.program_course.program:
            program_info = str(entry.course_allocation.program_course.program)
        
        return JsonResponse({
            'success': True,
            'message': 'Class entry updated successfully',
            'entry': {
                'id': entry.id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'lecturer': str(entry.course_allocation.lecturer) if entry.course_allocation.lecturer else 'Unassigned',
                'program': program_info,
                'venue': entry.venue.code,
                'venue_id': entry.venue.id,
                'date': entry.date.strftime('%Y-%m-%d'),
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'students': entry.course_allocation.number_of_students,
                'approved_by': str(entry.approved_by) if entry.approved_by else '',
                'approved_at': entry.approved_at.strftime('%Y-%m-%d %H:%M') if entry.approved_at else ''
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def update_exam_entry(request, pk):
    """AJAX handler for updating exam entry"""
    try:
        entry = get_object_or_404(ODELExamTimetable, pk=pk)
        data = json.loads(request.body)
        
        # Validate date is within range
        config = ODELTimetableConfig.objects.first()
        date_obj = datetime.datetime.strptime(data['date'], '%Y-%m-%d').date()
        is_valid, error_msg = validate_date_in_range(date_obj, config)
        
        if not is_valid:
            return JsonResponse({'success': False, 'error': error_msg})
        
        # Comprehensive conflict checking
        conflicts = check_conflicts(
            allocation=data['course_allocation'],
            venue=data['venue'],
            date=data['date'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            exam_mode=True,
            exclude_id=pk
        )
        
        if conflicts:
            return JsonResponse({
                'success': False, 
                'error': ' | '.join(conflicts)
            })
        
        # Update entry
        entry.course_allocation_id = data['course_allocation']
        entry.venue_id = data['venue']
        entry.date = data['date']
        entry.start_time = data['start_time']
        entry.end_time = data['end_time']
        entry.save()
        
        # Get updated data
        entry.refresh_from_db()
        entry = ODELExamTimetable.objects.select_related(
            'course_allocation__program_course__program',
            'course_allocation__lecturer',
            'venue'
        ).get(id=entry.id)
        
        # Get program info
        program_info = 'N/A'
        if entry.course_allocation.program_course and entry.course_allocation.program_course.program:
            program_info = str(entry.course_allocation.program_course.program)
        
        return JsonResponse({
            'success': True,
            'message': 'Exam entry updated successfully',
            'entry': {
                'id': entry.id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'lecturer': str(entry.course_allocation.lecturer) if entry.course_allocation.lecturer else 'Unassigned',
                'program': program_info,
                'venue': entry.venue.code,
                'venue_id': entry.venue.id,
                'date': entry.date.strftime('%Y-%m-%d'),
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'students': entry.course_allocation.number_of_students,
                'approved_by': str(entry.approved_by) if entry.approved_by else '',
                'approved_at': entry.approved_at.strftime('%Y-%m-%d %H:%M') if entry.approved_at else ''
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def delete_class_entry(request, pk):
    """AJAX handler for deleting class entry"""
    try:
        entry = get_object_or_404(ODELTimetable, pk=pk)
        entry.delete()
        return JsonResponse({'success': True, 'message': 'Class entry deleted successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def delete_exam_entry(request, pk):
    """AJAX handler for deleting exam entry"""
    try:
        entry = get_object_or_404(ODELExamTimetable, pk=pk)
        entry.delete()
        return JsonResponse({'success': True, 'message': 'Exam entry deleted successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('odel_system.clear_timetable')
@require_http_methods(["POST"])
def clear_timetable(request):
    """AJAX handler for clearing timetable entries"""
    try:
        data = json.loads(request.body)
        target = data.get('target', 'class')  # 'class', 'exam', 'both'
        
        results = {}
        
        with transaction.atomic():
            if target in ['class', 'both']:
                class_count = ODELTimetable.objects.count()
                ODELTimetable.objects.all().delete()
                results['class'] = class_count
            
            if target in ['exam', 'both']:
                exam_count = ODELExamTimetable.objects.count()
                ODELExamTimetable.objects.all().delete()
                results['exam'] = exam_count
        
        message_parts = []
        for key, count in results.items():
            if count > 0:
                message_parts.append(f"{count} {key} entries")
        
        if message_parts:
            message = "Cleared: " + ", ".join(message_parts)
        else:
            message = "No entries to clear"
        
        return JsonResponse({
            'success': True,
            'message': message,
            'results': results
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_timetable_data(request):
    """AJAX endpoint to get all timetable data"""
    entry_type = request.GET.get('type', 'class')  # 'class' or 'exam'
    
    config = ODELTimetableConfig.objects.first()
    if not config:
        return JsonResponse({'error': 'No configuration found'}, status=400)
    
    # Get all venues
    venues = Venue.objects.filter(capacity__isnull=False).order_by('code')
    
    # Get entries grouped by date
    if entry_type == 'class':
        entries = ODELTimetable.objects.all().select_related(
            'course_allocation__program_course__program',
            'course_allocation__lecturer',
            'venue',
            'approved_by'
        ).order_by('date', 'start_time')
        
        entries_data = []
        for entry in entries:
            program_info = 'N/A'
            if entry.course_allocation.program_course and entry.course_allocation.program_course.program:
                program_info = str(entry.course_allocation.program_course.program)
            
            entries_data.append({
                'id': entry.id,
                'course_allocation_id': entry.course_allocation_id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'lecturer': str(entry.course_allocation.lecturer) if entry.course_allocation.lecturer else 'Unassigned',
                'program': program_info,
                'venue': entry.venue.code,
                'venue_id': entry.venue.id,
                'date': entry.date.strftime('%Y-%m-%d'),
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'students': entry.course_allocation.number_of_students,
                'approved_by': str(entry.approved_by) if entry.approved_by else '',
                'approved_at': entry.approved_at.strftime('%Y-%m-%d %H:%M') if entry.approved_at else ''
            })
    else:  # exam
        entries = ODELExamTimetable.objects.all().select_related(
            'course_allocation__program_course__program',
            'course_allocation__lecturer',
            'venue',
            'approved_by'
        ).order_by('date', 'start_time')
        
        entries_data = []
        for entry in entries:
            program_info = 'N/A'
            if entry.course_allocation.program_course and entry.course_allocation.program_course.program:
                program_info = str(entry.course_allocation.program_course.program)
            
            entries_data.append({
                'id': entry.id,
                'course_allocation_id': entry.course_allocation_id,
                'course': entry.course_allocation.course_code,
                'course_name': entry.course_allocation.course_name,
                'lecturer': str(entry.course_allocation.lecturer) if entry.course_allocation.lecturer else 'Unassigned',
                'program': program_info,
                'venue': entry.venue.code,
                'venue_id': entry.venue.id,
                'date': entry.date.strftime('%Y-%m-%d'),
                'start': entry.start_time.strftime('%H:%M'),
                'end': entry.end_time.strftime('%H:%M'),
                'students': entry.course_allocation.number_of_students,
                'approved_by': str(entry.approved_by) if entry.approved_by else '',
                'approved_at': entry.approved_at.strftime('%Y-%m-%d %H:%M') if entry.approved_at else ''
            })
    
    # Group entries by date for easier rendering
    entries_by_date = {}
    for entry in entries_data:
        date = entry['date']
        if date not in entries_by_date:
            entries_by_date[date] = []
        entries_by_date[date].append(entry)
    
    return JsonResponse({
        'success': True,
        'entries': entries_data,
        'entries_by_date': entries_by_date,
        'venues': [{'id': v.id, 'code': v.code, 'capacity': v.capacity} for v in venues]
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def check_availability(request):
    """AJAX endpoint to check venue availability with comprehensive conflict checking"""
    venue_id = request.GET.get('venue_id')
    date = request.GET.get('date')
    start_time = request.GET.get('start_time')
    end_time = request.GET.get('end_time')
    exam_mode = request.GET.get('exam_mode') == 'true'
    allocation_id = request.GET.get('allocation_id')
    exclude_id = request.GET.get('exclude_id')
    
    if not all([venue_id, date, start_time, end_time, allocation_id]):
        return JsonResponse({'available': False, 'error': 'Missing parameters'})
    
    conflicts = check_conflicts(
        allocation=allocation_id,
        venue=venue_id,
        date=date,
        start_time=start_time,
        end_time=end_time,
        exam_mode=exam_mode,
        exclude_id=exclude_id if exclude_id else None
    )
    
    return JsonResponse({
        'available': len(conflicts) == 0,
        'conflicts': conflicts
    })


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def get_time_slots(request):
    """AJAX endpoint to get available time slots for a date"""
    date_str = request.GET.get('date')
    slot_type = request.GET.get('type', 'class')  # 'class' or 'exam'
    
    if not date_str:
        return JsonResponse({'slots': []})
    
    try:
        date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        config = ODELTimetableConfig.objects.first()
        
        if not config:
            return JsonResponse({'slots': []})
        
        # Validate date is within range
        is_valid, error_msg = validate_date_in_range(date, config)
        if not is_valid:
            return JsonResponse({'error': error_msg})
        
        # Generate slots based on type
        if slot_type == 'class':
            time_slots = generate_class_time_slots(config, date)
        else:
            time_slots = generate_exam_time_slots(config, date)
        
        return JsonResponse({
            'slots': [
                {
                    'start': slot['start'].strftime('%H:%M'),
                    'end': slot['end'].strftime('%H:%M'),
                    'display': slot['display'],
                    'slot_number': slot['slot_number']
                }
                for slot in time_slots
            ]
        })
    except Exception as e:
        return JsonResponse({'error': str(e)})


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["POST"])
def update_timetable_config(request):
    """AJAX handler for updating timetable configuration"""
    try:
        data = json.loads(request.body)
        
        config = ODELTimetableConfig.objects.first()
        if not config:
            config = ODELTimetableConfig()
        
        # Update config fields
        config.start_date = data.get('start_date', config.start_date)
        config.end_date = data.get('end_date', config.end_date)
        config.day_start_time = data.get('day_start_time', config.day_start_time)
        config.day_end_time = data.get('day_end_time', config.day_end_time)
        config.class_slot_size = int(data.get('class_slot_size', config.class_slot_size))
        config.exam_slot_size = int(data.get('exam_slot_size', config.exam_slot_size))
        config.exam_break_duration = int(data.get('exam_break_duration', config.exam_break_duration))
        
        config.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Configuration updated successfully',
            'config': {
                'start_date': config.start_date.strftime('%Y-%m-%d'),
                'end_date': config.end_date.strftime('%Y-%m-%d'),
                'day_start_time': config.day_start_time.strftime('%H:%M'),
                'day_end_time': config.day_end_time.strftime('%H:%M'),
                'class_slot_size': config.class_slot_size,
                'exam_slot_size': config.exam_slot_size,
                'exam_break_duration': config.exam_break_duration
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})