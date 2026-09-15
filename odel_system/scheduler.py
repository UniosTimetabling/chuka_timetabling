from core.rbac import allowed_roles, Role
# ----- Auto Scheduling Views -----

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('odel_timetable.add_odeltemptimetable')
def run_auto_schedule(request):
    """Run the ODEL auto-scheduler"""
    if request.method == 'POST':
        result = run_odel_autoscheduler()
        
        if result['status'] == 'success':
            messages.success(request, result['message'])
            return redirect('odel_management:timetable_preview')
        else:
            messages.error(request, result['message'])
            return redirect('odel_management:timetable_config')
    
    # Get counts for display
    allocations = ODELCourseAllocation.objects.filter(
        submitted_to_tt=True,
        approved_by_dvc=True,
        rejected=False
    ).count()
    
    scheduled = ODELTempTimetable.objects.count()
    
    context = {
        'total_allocations': allocations,
        'already_scheduled': scheduled,
        'to_schedule': allocations - scheduled,
    }
    return render(request, 'odel_management/run_auto_schedule.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('odel_timetable.add_odeltemptimetable')
def run_manual_schedule(request):
    """Run manual scheduling assistant (suggests slots)"""
    if request.method == 'POST':
        allocation_id = request.POST.get('course_allocation')
        allocation = get_object_or_404(ODELCourseAllocation, pk=allocation_id)
        
        result = run_odel_manual_schedule(allocation)
        
        return JsonResponse(result)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def check_availability(request):
    """AJAX endpoint to check venue availability"""
    venue_id = request.GET.get('venue_id')
    date = request.GET.get('date')
    start_time = request.GET.get('start_time')
    end_time = request.GET.get('end_time')
    
    if not all([venue_id, date, start_time, end_time]):
        return JsonResponse({'available': False, 'error': 'Missing parameters'})
    
    # Check for conflicts
    conflicts = ODELTempTimetable.objects.filter(
        venue_id=venue_id,
        date=date,
        start_time__lt=end_time,
        end_time__gt=start_time
    ).exists()
    
    if not conflicts:
        conflicts = ODELTimetable.objects.filter(
            venue_id=venue_id,
            date=date,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).exists()
    
    return JsonResponse({'available': not conflicts})


# ----- Exam Timetable Views -----

@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_timetable_list(request):
    """List all approved ODEL exam timetables"""
    exam_timetables = ODELExamTimetable.objects.select_related(
        'course_allocation__program_course',
        'venue',
        'approved_by'
    ).all().order_by('date', 'start_time')
    
    context = {
        'exam_timetables': exam_timetables,
        'can_approve': request.user.has_perm('odel_timetable.approve_odel_exam'),
    }
    return render(request, 'odel_management/exam_timetable_list.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def exam_temp_timetable_list(request):
    """List temporary exam timetables"""
    temp_entries = ODELExamTempTimetable.objects.select_related(
        'course_allocation__program_course',
        'venue'
    ).all().order_by('date', 'start_time')
    
    context = {
        'temp_entries': temp_entries,
        'can_approve': request.user.has_perm('odel_timetable.approve_odel_exam'),
    }
    return render(request, 'odel_management/exam_temp_list.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@permission_required('odel_timetable.add_odelexamtemptimetable')
def exam_manual_create(request):
    """Manually create exam timetable entry"""
    if request.method == 'POST':
        allocation_id = request.POST.get('course_allocation')
        venue_id = request.POST.get('venue')
        date = request.POST.get('date')
        start_time = request.POST.get('start_time')
        end_time = request.POST.get('end_time')
        
        try:
            with transaction.atomic():
                entry = ODELExamTempTimetable.objects.create(
                    course_allocation_id=allocation_id,
                    venue_id=venue_id,
                    day=request.POST.get('day'),
                    date=date,
                    start_time=start_time,
                    end_time=end_time
                )
                messages.success(request, 'Exam timetable entry created')
                return redirect('odel_management:exam_temp_list')
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
    
    allocations = ODELCourseAllocation.objects.filter(
        submitted_to_tt=True,
        approved_by_dvc=True,
        rejected=False
    ).select_related('program_course')
    
    venues = Venue.objects.filter(capacity__isnull=False).order_by('code')
    
    context = {
        'allocations': allocations,
        'venues': venues,
    }
    return render(request, 'odel_management/exam_manual_form.html', context)


@allowed_roles(Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def run_exam_auto_schedule(request):
    """Run ODEL exam auto-scheduler"""
    if request.method == 'POST':
        result = run_odel_exam_scheduler()
        
        if result['status'] == 'success':
            messages.success(request, result['message'])
            return redirect('odel_management:exam_temp_list')
        else:
            messages.error(request, result['message'])
    
    return render(request, 'odel_management/run_exam_schedule.html')

