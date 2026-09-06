#!/usr/bin/env python
"""
EXAM AUTO-SCHEDULER DOCUMENTATION SCRIPT
This script creates comprehensive documentation for the Exam Auto-Scheduler system
including configuration, scheduling algorithms, administration panels, and APIs.
Run: python manage.py shell < this_script.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
django.setup()

from documentation.models import (
    DocumentationCategory, DocumentationPage, DocumentationSection, 
    CodeExample, DocumentationTag, PageTag, InternalLink
)
from django.contrib.auth.models import User
from django.utils import timezone

print("=" * 80)
print("EXAM AUTO-SCHEDULER DOCUMENTATION")
print("=" * 80)

# Get admin user for documentation
try:
    author = User.objects.filter(is_superuser=True).first()
    if not author:
        print("ERROR: No admin user found. Please create an admin user first.")
        exit()
    print(f"Using admin: {author.username}")
except Exception as e:
    print(f"Error with user: {e}")
    exit()

# ============================================================
# 1. CHECK AND CREATE CATEGORIES
# ============================================================

print("\n1. CHECKING AND CREATING CATEGORIES...")

categories_data = [
    {
        'name': 'Exam Auto-Scheduler Configuration',
        'slug': 'exam-auto-scheduler-configuration',
        'description': 'Configuration and setup for automated exam scheduling',
        'icon': 'fas fa-cogs',
        'order': 31,
        'access_level': 'admin',
    },
    {
        'name': 'Scheduling Algorithms',
        'slug': 'scheduling-algorithms',
        'description': 'Core algorithms for exam scheduling and conflict resolution',
        'icon': 'fas fa-brain',
        'order': 32,
        'access_level': 'admin',
    },
    {
        'name': 'Exam Administration Panels',
        'slug': 'exam-administration-panels',
        'description': 'Admin interfaces for managing exam timetables',
        'icon': 'fas fa-tasks',
        'order': 33,
        'access_level': 'management',
    },
    {
        'name': 'Data Export & Publishing',
        'slug': 'data-export-publishing',
        'description': 'Exporting and publishing exam timetables',
        'icon': 'fas fa-file-export',
        'order': 34,
        'access_level': 'management',
    },
    {
        'name': 'COD Exam Management',
        'slug': 'cod-exam-management',
        'description': 'Chair of Department exam timetable operations',
        'icon': 'fas fa-user-tie',
        'order': 35,
        'access_level': 'department',
    },
]

categories = {}
for cat_data in categories_data:
    category, created = DocumentationCategory.objects.get_or_create(
        slug=cat_data['slug'],
        defaults=cat_data
    )
    
    if not created:
        updated = False
        for field, value in cat_data.items():
            if getattr(category, field) != value:
                setattr(category, field, value)
                updated = True
        if updated:
            category.save()
            print(f"   Updated: {category.name}")
    else:
        print(f"   Created: {category.name}")
    
    categories[cat_data['slug']] = category

# ============================================================
# 2. ADD OR GET EXISTING TAGS
# ============================================================

print("\n2. ADDING OR GETTING EXISTING TAGS...")

tags_data = [
    {'name': 'Exam Scheduling', 'slug': 'exam-scheduling', 'color': '#28a745'},
    {'name': 'Auto-Scheduler', 'slug': 'auto-scheduler', 'color': '#6f42c1'},
    {'name': 'Conflict Detection', 'slug': 'conflict-detection', 'color': '#17a2b8'},
    {'name': 'Venue Management', 'slug': 'venue-management', 'color': '#fd7e14'},
    {'name': 'PDF Export', 'slug': 'pdf-export', 'color': '#e83e8c'},
    {'name': 'CSV Export', 'slug': 'csv-export', 'color': '#20c997'},
    {'name': 'COD Panel', 'slug': 'cod-panel', 'color': '#6c757d'},
    {'name': 'Batch Processing', 'slug': 'batch-processing', 'color': '#dc3545'},
    {'name': 'Time Slots', 'slug': 'time-slots', 'color': '#007bff'},
    {'name': 'Data Models', 'slug': 'data-models', 'color': '#6610f2'},
    {'name': 'API Endpoints', 'slug': 'api-endpoints', 'color': '#ffc107'},
]

tags = {}
for tag_data in tags_data:
    tag, created = DocumentationTag.objects.get_or_create(
        slug=tag_data['slug'],
        defaults=tag_data
    )
    tags[tag_data['slug']] = tag
    status = "Created" if created else "Exists"
    print(f"   {status}: {tag.name}")

# Get existing tags for reference
existing_tags = {}
for tag in DocumentationTag.objects.all():
    existing_tags[tag.slug] = tag

# ============================================================
# 3. CREATE DOCUMENTATION PAGES
# ============================================================

print("\n3. CREATING DOCUMENTATION PAGES...")

# Page 1: Exam Auto-Scheduler Home & Configuration
page1_content = """
<h2>Exam Auto-Scheduler Home & Configuration</h2>
<p>Main interface for exam auto-scheduling with configuration management.</p>

<h3>Overview</h3>
<p>The <code>exam_autoscheduler_home</code> view provides the main interface for:</p>
<ol>
<li><strong>Configuration management</strong>: Set exam dates, times, and slots</li>
<li><strong>Slot generation</strong>: Automatic time slot creation with breaks</li>
<li><strong>Data display</strong>: Show schedules, venues, and excluded dates</li>
<li><strong>Navigation</strong>: Links to related functions</li>
</ol>

<h3>Configuration Model</h3>
<p>Uses <code>ExamSchedulerConfig</code> model with key fields:</p>
<pre><code class="python">class ExamSchedulerConfig(models.Model):
    start_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_size = models.IntegerField()  # in hours
    max_exam_days = models.IntegerField()
    excluded_days = models.TextField(blank=True)  # CSV of dates</code></pre>

<h3>Slot Generation Algorithm</h3>
<p>The <code>generate_slots()</code> function creates exam slots with breaks:</p>
<pre><code class="python">def generate_slots(start_time, end_time, slot_size):
    slots = []
    current = datetime.datetime.combine(datetime.date.today(), start_time)
    end = datetime.datetime.combine(datetime.date.today(), end_time)
    slot_delta = datetime.timedelta(hours=slot_size)
    break_delta = datetime.timedelta(minutes=60)  # 60-minute break

    while current + slot_delta <= end:
        nxt = current + slot_delta
        slots.append((current.time(), nxt.time()))
        current = nxt + break_delta  # Add break between slots

    return slots</code></pre>

<h3>Date Range Calculation</h3>
<p>Calculate available exam dates excluding weekends and holidays:</p>
<pre><code class="python">date_range = config.get_date_range()
excluded_list = config.excluded_date_list()

# Convert to date-day mapping
date_day_map = {
    d: datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%A")
    for d in existing_dates
}
days_with_names = [(d, date_day_map[d]) for d in existing_dates]</code></pre>

<h3>Configuration Update API</h3>
<p>AJAX endpoint for updating configuration:</p>
<pre><code class="python">@csrf_exempt
def update_exam_config(request):
    if request.method == "POST":
        config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
        
        # Update fields from POST data
        if start_date:
            config.start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        if start_time:
            config.start_time = datetime.datetime.strptime(start_time, "%H:%M").time()
        # ... other fields
        
        config.save()
        return JsonResponse({"status": "success", "message": "Configuration saved!"})</code></pre>

<h3>API Endpoints for Data Loading</h3>
<h4>Venues API</h4>
<pre><code class="python">def api_exam_venues(request):
    venues = list(
        Venue.objects.all().order_by("code")
        .values("id", "code", "capacity")
    )
    return JsonResponse({"venues": venues})</code></pre>

<h4>Temporary Data API</h4>
<pre><code class="python">def api_exam_temp_data(request):
    temp = ExamTempTimetable.objects.select_related(
        "course_allocation", "venue"
    ).values(
        "id", "date", "start_time", "venue__code", "venue_id",
        "course_allocation__course_code",
        "course_allocation__number_of_students",
        "course_allocation__lecturer"
    )
    return JsonResponse({"temp_data": list(temp)})</code></pre>

<h4>Merged Groups API</h4>
<pre><code class="python">def api_exam_merged_groups(request):
    groups = []
    for m in MergedCourseGroup.objects.select_related("venue").prefetch_related("merged_courses"):
        groups.append({
            "id": m.id,
            "date": m.date,
            "start_time": m.start_time,
            "venue": m.venue.code if m.venue else None,
            "base_code": m.base_course.course_code,
            "merged": [c.course_code for c in m.merged_courses.all()],
        })
    return JsonResponse({"merged_groups": groups})</code></pre>

<h3>Template Context</h3>
<p>Key data passed to template:</p>
<pre><code class="python">return render(request, "exam_autosheduler.html", {
    "config": config,
    "slots": slots,
    "tableslots": tableslots,  # Formatted for display
    "date_range": date_range,
    "excluded_list": excluded_list,
    "days_with_names": days_with_names,
    "navbar_links": {
        "Go to Schedule main timetable": "autoscheduler_home",
        "Publish to Exam": "publish_to_exam_main",
    },
})</code></pre>

<h3>Use Cases</h3>
<h4>Initial Setup</h4>
<ol>
<li>Configure exam period dates</li>
<li>Set daily time slots and break duration</li>
<li>Specify excluded dates (holidays, weekends)</li>
<li>Define maximum exam days</li>
</ol>

<h4>Regular Updates</h4>
<ol>
<li>Adjust dates for new semester</li>
<li>Modify time slots based on requirements</li>
<li>Add/remove excluded dates</li>
<li>Update venue capacities</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Plan exam period early</strong>: Set dates at least 2 weeks in advance</li>
<li><strong>Include buffer days</strong>: Add extra days for rescheduling</li>
<li><strong>Consider venue availability</strong>: Coordinate with venue management</li>
<li><strong>Test configuration</strong>: Run test scheduling before production</li>
<li><strong>Document changes</strong>: Keep change log of configuration updates</li>
</ol>
"""

page1 = DocumentationPage.objects.create(
    title='Exam Auto-Scheduler Home & Configuration',
    slug='exam-auto-scheduler-home-configuration',
    short_description='Main interface for configuring and managing exam auto-scheduling',
    content=page1_content,
    category=categories['exam-auto-scheduler-configuration'],
    page_type='guide',
    difficulty='beginner',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=15,
    version='1.0'
)
print(f"   Created: {page1.title}")

# Page 2: Optimized Scheduler Core Algorithms
page2_content = """
<h2>Optimized Scheduler Core Algorithms</h2>
<p>Core scheduling algorithms with conflict avoidance and batch processing.</p>

<h3>Overview</h3>
<p>The optimized scheduler implements intelligent exam scheduling with:</p>
<ol>
<li><strong>Conflict avoidance</strong>: Lecturer, program, venue collisions</li>
<li><strong>Batch processing</strong>: Efficient handling of large course sets</li>
<li><strong>Program year distribution</strong>: Balanced scheduling across years</li>
<li><strong>Room combination</strong>: Multi-venue allocation for large courses</li>
</ol>

<h3>OptimizedSchedulerState Class</h3>
<p>Manages scheduler state with efficient conflict tracking:</p>
<pre><code class="python">class OptimizedSchedulerState:
    def __init__(self, config):
        self.config = config
        self.spacing_ratio = getattr(config, "spacing_ratio", 0.7)
        
        # Precompute venue data
        self.venues = list(Venue.objects.filter(capacity__isnull=False).order_by("capacity"))
        self.venue_effcap = {v.id: venue_effective_capacity(v, self.spacing_ratio) for v in self.venues}
        
        # Conflict tracking dictionaries
        self.lecturer_busy = defaultdict(set)  # {lecturer_id: set((date, slot_start))}
        self.program_busy = defaultdict(set)   # {program_id: set((date, slot_start))}
        self.venue_usage = defaultdict(int)    # {(venue_id, date, slot_start): used_capacity}
        
        # Program year distribution
        self.program_year_schedule_count = defaultdict(int)
        self.program_year_daily_count = defaultdict(lambda: defaultdict(int))</code></pre>

<h3>Course Grouping and Prioritization</h3>
<h4>Group by Course Family</h4>
<pre><code class="python">def group_courses_by_family(courses):
    groups = defaultdict(list)
    for c in courses:
        key = normalize_course_code(c.course_code or c.course_name)
        groups[key].append(c)
    
    # Sort groups by total students (largest first)
    def total_students(group):
        return sum(getattr(c, "number_of_students", 0) or 1 for c in group)
    
    return sorted(groups.items(), key=lambda kv: -total_students(kv[1]))</code></pre>

<h4>Course Prioritization</h4>
<pre><code class="python">def prioritize_courses(courses):
    prioritized = []
    for course in courses:
        student_count = getattr(course, "number_of_students", 0) or 1
        priority_score = student_count  # Larger courses get higher priority
        
        # Afternoon courses are slightly harder to place
        if is_afternoon_course(course.course_code):
            priority_score += 1000
            
        prioritized.append((priority_score, course))
    
    return [course for _, course in sorted(prioritized, reverse=True)]</code></pre>

<h3>Room Allocation Algorithms</h3>
<h4>Single Room Allocation</h4>
<pre><code class="python">def best_single_room(venues_with_effcap, needed):
    candidates = [v for v, eff in venues_with_effcap if eff >= needed]
    if not candidates:
        return None
    return min(candidates, key=lambda v: v.capacity)  # Best-fit</code></pre>

<h4>Room Combination</h4>
<pre><code class="python">def greedy_combine_rooms(venues_with_effcap, needed):
    sorted_venues = sorted(venues_with_effcap, key=lambda ve: ve[1], reverse=True)
    chosen = []
    total = 0
    for v, eff in sorted_venues:
        chosen.append(v)
        total += eff
        if total >= needed:
            return chosen
    return []</code></pre>

<h3>Batch Processing Core</h3>
<pre><code class="python">def process_course_batch(batch_courses, state, scheduled_course_ids, batch_id, total_batches):
    start_time = time.time()
    MAX_BATCH_TIME = 120  # 2 minutes per batch max
    success_count = 0
    unallocated = []
    
    # Group courses by program year for balanced distribution
    program_year_batches = distribute_courses_by_program_year(batch_courses)
    
    for program_year, year_courses in program_year_batches.items():
        course_groups = group_courses_by_family(year_courses)
        total_groups = len(course_groups)
        
        for i, (group_key, group_courses) in enumerate(course_groups):
            # Check for timeout
            if time.time() - start_time > MAX_BATCH_TIME:
                unallocated.extend(group_courses)
                continue
                
            # Try to schedule the entire group
            if schedule_course_group_with_program_year(
                group_key, group_courses, state, scheduled_course_ids, program_year
            ):
                success_count += len(group_courses)
            else:
                # Fallback: individual scheduling
                individual_success = 0
                for course in prioritize_courses(group_courses):
                    if schedule_single_course_with_program_year(
                        course, state, scheduled_course_ids, program_year
                    ):
                        individual_success += 1
                    else:
                        unallocated.append(course)
                
                success_count += individual_success
    
    return success_count, unallocated</code></pre>

<h3>Program Year Distribution</h3>
<pre><code class="python">def get_program_year(course_allocation):
    program = getattr(course_allocation, 'program', None)
    if not program:
        return "unknown"
    
    program_name = getattr(program, 'name', '') or getattr(program, 'code', '')
    if not program_name:
        return "unknown"
    
    # Look for year patterns
    year_patterns = [
        (r'.*[Yy]ear\s*(\d+).*', 1),  # "Year 1", "year 2"
        (r'.*[Yy](\d+).*', 1),        # "Y1", "y2"
        (r'.*(\d)[Rr][Dd].*', 1),     # "3rd", "1rd"
        (r'.*(\d)[Tt][Hh].*', 1),     # "4th", "2th"
    ]
    
    for pattern, group in year_patterns:
        match = re.match(pattern, str(program_name))
        if match:
            return f"year_{match.group(group)}"
    
    return "unknown"</code></pre>

<h3>Conflict Detection</h3>
<pre><code class="python">def is_group_available(group_courses, date_obj, slot_start, state, group_key):
    for course in group_courses:
        lect_id = getattr(course.lecturer, "id", None)
        prog_id = getattr(course.program, "id", None)
        
        if not state.is_lecturer_available(lect_id, date_obj, slot_start, group_key):
            return False
        if not state.is_program_available(prog_id, date_obj, slot_start, group_key):
            return False
    
    return True</code></pre>

<h3>Afternoon Course Detection</h3>
<pre><code class="python">def is_afternoon_course(course_code):
    digits = extract_leading_digits(course_code)
    return digits.startswith(("8", "9"))</code></pre>

<h3>Best Practices</h3>
<ol>
<li><strong>Batch size optimization</strong>: Adjust based on system performance</li>
<li><strong>Timeout protection</strong>: Prevent infinite loops</li>
<li><strong>Memory management</strong>: Clear unused data between batches</li>
<li><strong>Progress tracking</strong>: Update progress for long operations</li>
<li><strong>Error recovery</strong>: Handle partial failures gracefully</li>
</ol>
"""

page2 = DocumentationPage.objects.create(
    title='Optimized Scheduler Core Algorithms',
    slug='optimized-scheduler-core-algorithms',
    short_description='Core algorithms for exam scheduling with conflict avoidance',
    content=page2_content,
    category=categories['scheduling-algorithms'],
    page_type='reference',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=20,
    version='1.0'
)
print(f"   Created: {page2.title}")

# Page 3: Exam Timetable Administration Panel
page3_content = """
<h2>Exam Timetable Administration Panel</h2>
<p>Administration interface for managing exam timetables with AJAX operations.</p>

<h3>Overview</h3>
<p>The <code>exam_timetable_panel</code> view provides:</p>
<ol>
<li><strong>Full timetable management</strong>: View, add, edit, delete exams</li>
<li><strong>Merged course management</strong>: Handle grouped exams</li>
<li><strong>Shared venue management</strong>: Manage exams sharing venues</li>
<li><strong>AJAX operations</strong>: Real-time updates without page reload</li>
<li><strong>Filtering and search</strong>: Find specific exams quickly</li>
</ol>

<h3>Access Control</h3>
<pre><code class="python">@login_required
@group_required("Director Timetable", "Timetable Admins")
def exam_timetable_panel(request):
    # Protected view for authorized users only</code></pre>

<h3>Time Slot Generation</h3>
<pre><code class="python"># Generate time slots from configuration
config, _ = ExamSchedulerConfig.objects.get_or_create(id=1)
start_dt = datetime.combine(config.start_date, config.start_time)
end_dt = datetime.combine(config.start_date, config.end_time)
slot_interval = config.slot_size  # in hours
break_interval = timedelta(minutes=60)
time_slots = []

current = start_dt
while current.time() < config.end_time:
    end_slot = current + timedelta(hours=slot_interval)
    if end_slot.time() > config.end_time:
        break
    label = f"{current.strftime('%I:%M%p')} - {end_slot.strftime('%I:%M%p')}"
    time_slots.append((current.strftime("%H:%M"), label))
    current = end_slot + break_interval</code></pre>

<h3>Data Loading with Optimized Queries</h3>
<pre><code class="python"># Load timetables with eager loading
timetables = (
    ExamTimetable.objects
    .select_related("course_allocation", "course_allocation__lecturer")
    .all()
    .order_by("date", "start_time")
)

# Get all course allocations
all_allocations = CourseAllocation.objects.select_related("lecturer", "department").all()

# Identify already scheduled courses
scheduled_ids = set(
    ExamTimetable.objects
    .exclude(course_allocation__isnull=True)
    .values_list("course_allocation_id", flat=True)
    .distinct()
)</code></pre>

<h3>Merged Course Group Management</h3>
<pre><code class="python"># Get published merged groups
merged_groups = (
    MergedCourseGroup.objects.filter(published=True)
    .select_related("base_course", "venue")
    .prefetch_related(
        Prefetch(
            "merged_courses",
            queryset=CourseAllocation.objects.select_related("lecturer")
        )
    )
    .order_by("date", "start_time")
)</code></pre>

<h3>AJAX Add Exam Handler</h3>
<pre><code class="python">if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
    try:
        alloc = get_object_or_404(CourseAllocation, id=request.POST["allocation_id"])
        venue = request.POST["venue"]
        exam_date = request.POST["exam_date"]
        slot_start = datetime.strptime(request.POST["slot_time"], "%H:%M").time()
        slot_end = (
            datetime.combine(datetime.today(), slot_start)
            + timedelta(hours=config.slot_size)
        ).time()

        # Check for conflicts
        if ExamTimetable.objects.filter(
            venue=venue, date=exam_date, start_time=slot_start
        ).exists():
            return JsonResponse({
                "status": "error",
                "messages": [f"Conflict: {venue} already booked at {slot_start} on {exam_date}."],
            })

        # Create exam timetable record
        ExamTimetable.objects.create(
            course_allocation=alloc,
            venue=venue,
            day=datetime.strptime(exam_date, "%Y-%m-%d").strftime("%A"),
            date=exam_date,
            start_time=slot_start,
            end_time=slot_end,
        )

        return JsonResponse({
            "status": "success",
            "messages": [f"Exam scheduled for {alloc.course_code} on {exam_date}."],
        })

    except Exception as e:
        return JsonResponse({"status": "error", "messages": [str(e)]})</code></pre>

<h3>Merged Group Operations</h3>
<h4>Remove from Merged Group</h4>
<pre><code class="python">@require_POST
def remove_from_merged(request):
    group_id = request.POST.get("group_id")
    course_id = request.POST.get("course_id")
    try:
        mg = MergedCourseGroup.objects.get(id=group_id)
        course = CourseAllocation.objects.get(id=course_id)
        mg.merged_courses.remove(course)
        mg.total_students = sum(c.number_of_students for c in mg.merged_courses.all())
        mg.save()
        return JsonResponse({"status": "success"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})</code></pre>

<h4>Add to Merged Group</h4>
<pre><code class="python">@require_POST
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
        return JsonResponse({"status": "error", "message": str(e)})</code></pre>

<h3>Delete Operations</h3>
<h4>Delete Single Entry</h4>
<pre><code class="python">@login_required
@require_POST
@transaction.atomic
def delete_exam_entry(request):
    entry_id = request.POST.get("id")
    try:
        entry = ExamTimetable.objects.select_related("course_allocation").get(id=entry_id)
        
        # Delete related merged groups
        course_allocation = getattr(entry, "course_allocation", None)
        if course_allocation:
            merged_qs = MergedCourseGroup.objects.filter(
                models.Q(base_course=course_allocation) |
                models.Q(merged_courses=course_allocation)
            )
            if merged_qs.exists():
                merged_qs.delete()
        
        # Delete the main entry
        entry.delete()
        return JsonResponse({"status": "success"})
        
    except ExamTimetable.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Entry not found"})</code></pre>

<h4>Delete All Exams</h4>
<pre><code class="python">@login_required
@permission_required("TT_APP.approve_timetable", raise_exception=True)
def delete_all_Exam_timetables(request):
    if request.method == "POST":
        academic_year = request.POST.get("academic_year")
        semester = request.POST.get("semester")
        
        # Archive before deletion
        entries = list(ExamTimetable.objects.values())
        if entries:
            safe_entries = safe_serialize(entries)
            TimetableArchive.objects.create(
                timetable_type="EXAM",
                semester=semester,
                academic_year=academic_year,
                archived_by=request.user,
                data=safe_entries,
            )
        
        # Delete all exam data
        count, _ = ExamTimetable.objects.all().delete()
        MergedCourseGroup.objects.all().delete()
        SharedVenueExamGroup.objects.all().delete()
        
        messages.success(request, f"✅ Archived and deleted {count} exam timetable entries.")
        return redirect("exam_timetable_panel")</code></pre>

<h3>Template Context</h3>
<pre><code class="python">return render(
    request,
    "shedule_main_exam.html",
    {
        "heading": "Exam Timetable Management Panel",
        "time_slots": time_slots,
        "timetables": timetables,
        "unscheduled_allocations": unscheduled_allocations,
        "existing_venues": venues,
        "config": config,
        "date_range": date_range,
        "date_excluded_range": date_excluded_range,
        "excluded_list": excluded_list,
        "merged_groups": merged_groups,
        "shared_groups": shared_published_groups,
        "navbar_links": {
            "Main Timetable": "timetable_panel",
            "Download Exam PDF": "download_exam_timetable",
            "Export Exam CSV": "export_exam_timetable",
            "Autoschedule Exams": "exam_autoscheduler_home",
            "Delete All": "delete_all_exam_timetables",
        },
    },
)</code></pre>
"""

page3 = DocumentationPage.objects.create(
    title='Exam Timetable Administration Panel',
    slug='exam-timetable-administration-panel',
    short_description='Admin interface for managing exam timetables with AJAX operations',
    content=page3_content,
    category=categories['exam-administration-panels'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=18,
    version='1.0'
)
print(f"   Created: {page3.title}")

# Page 4: Export and Publishing Functions
page4_content = """
<h2>Export and Publishing Functions</h2>
<p>Functions for publishing temporary schedules and exporting to various formats.</p>

<h3>Overview</h3>
<p>The system provides multiple export and publishing options:</p>
<ol>
<li><strong>Publish to Main</strong>: Move from temporary to permanent timetable</li>
<li><strong>CSV Export</strong>: Export to spreadsheet format</li>
<li><strong>PDF Export</strong>: Generate formatted PDF documents</li>
<li><strong>Archive Management</strong>: Backup and restore operations</li>
</ol>

<h3>Publish to Main Timetable</h3>
<pre><code class="python">def exam_publish_to_main(request):
    # Delete existing main timetable
    ExamTimetable.objects.all().delete()

    # Fetch all temporary entries
    for entry in ExamTempTimetable.objects.select_related("course_allocation", "venue"):
        exists = ExamTimetable.objects.filter(
            course_allocation=entry.course_allocation,
            venue=entry.venue.code if entry.venue else "",
            day=entry.day,
            date=entry.date,
            start_time=entry.start_time,
            end_time=entry.end_time
        )

        if not exists.exists():
            ExamTimetable.objects.create(
                course_allocation=entry.course_allocation,
                venue=entry.venue.code if entry.venue else "",
                day=entry.day,
                date=entry.date,
                start_time=entry.start_time,
                end_time=entry.end_time
            )

        # Mark related groups as published
        merged_group = MergedCourseGroup.objects.filter(
            base_course=entry.course_allocation
        ).first()
        if merged_group and not merged_group.published:
            merged_group.published = True
            merged_group.save(update_fields=["published"])

    # Clear temporary data
    ExamTempTimetable.objects.all().delete()
    messages.success(request, "✅ Temporary timetable published to main timetable.")
    return redirect("exam_autoscheduler_home")</code></pre>

<h3>CSV Export Function</h3>
<pre><code class="python">def export_exam_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="exam_timetable.csv"'
    writer = csv.writer(response)
    
    # Write headers
    writer.writerow([
        "Course Code", "Course Name", "Lecturer", "Venue",
        "Day", "Date", "Start", "End"
    ])

    # Get base entries
    base_entries = ExamTempTimetable.objects.select_related(
        "course_allocation", "venue"
    ).order_by("date", "start_time")

    written_codes = set()

    for entry in base_entries:
        base_code = entry.course_allocation.course_code

        # Write base course
        if base_code not in written_codes:
            writer.writerow([
                base_code,
                entry.course_allocation.course_name,
                entry.course_allocation.lecturer.display_name
                if entry.course_allocation.lecturer else "Unassigned",
                entry.venue.code if entry.venue else "",
                entry.day,
                entry.date.strftime("%Y-%m-%d"),
                entry.start_time.strftime("%H:%M") if entry.start_time else "",
                entry.end_time.strftime("%H:%M") if entry.end_time else "",
            ])
            written_codes.add(base_code)

        # Include merged courses (published only)
        merged_group = MergedCourseGroup.objects.filter(
            base_course=entry.course_allocation, published=True
        ).first()

        if merged_group:
            for merged_course in merged_group.merged_courses.all():
                merged_code = merged_course.course_code
                if merged_code not in written_codes:
                    writer.writerow([
                        merged_code,
                        merged_course.course_name,
                        merged_course.lecturer.display_name
                        if merged_course.lecturer else "Unassigned",
                        entry.venue.code if entry.venue else "",
                        entry.day,
                        entry.date.strftime("%Y-%m-%d"),
                        entry.start_time.strftime("%H:%M") if entry.start_time else "",
                        entry.end_time.strftime("%H:%M") if entry.end_time else "",
                    ])
                    written_codes.add(merged_code)

    return response</code></pre>

<h3>PDF Export Function</h3>
<pre><code class="python">def export_exam_pdf(request):
    # Get logo URL
    logo_url = request.build_absolute_uri(static('chuka.png'))

    # Get base entries
    base_entries = ExamTempTimetable.objects.select_related(
        "course_allocation", "venue"
    ).order_by("date", "start_time")

    all_entries = []

    # Add base entries
    for entry in base_entries:
        all_entries.append(entry)

        # Add published merged courses
        merged_group = MergedCourseGroup.objects.filter(
            base_course=entry.course_allocation, published=True
        ).first()

        if merged_group:
            for merged_course in merged_group.merged_courses.all():
                clone = type(entry)(
                    id=None,
                    course_allocation=merged_course,
                    venue=entry.venue,
                    day=entry.day,
                    date=entry.date,
                    start_time=entry.start_time,
                    end_time=entry.end_time,
                )
                all_entries.append(clone)

    # Include lab sessions
    lab_sessions = LabExamTimetable.objects.select_related(
        "lab_allocation__program_course", "lab_venue"
    ).order_by("date", "start_time")

    for lab in lab_sessions:
        pseudo_entry = type("LabProxy", (), {})()
        pseudo_entry.course_allocation = lab.lab_allocation.program_course
        pseudo_entry.venue = lab.lab_venue
        pseudo_entry.day = lab.day
        pseudo_entry.date = lab.date
        pseudo_entry.start_time = lab.start_time
        pseudo_entry.end_time = lab.end_time
        pseudo_entry.is_lab = True
        all_entries.append(pseudo_entry)

    # De-duplicate entries
    unique_map = {}
    for e in all_entries:
        key = (
            getattr(e.venue, "code", str(e.venue)),
            e.date,
            e.start_time,
            getattr(e.course_allocation, "course_code", ""),
        )
        if key not in unique_map:
            unique_map[key] = e

    all_entries = list(unique_map.values())

    # Group by date
    date_range = sorted({e.date for e in all_entries})
    grouped_entries = []
    for d in date_range:
        day_name = d.strftime("%A")
        day_entries = [e for e in all_entries if e.date == d]
        grouped_entries.append((d, day_name, day_entries))

    # Render PDF
    html = render_to_string("exam_timetable_pdf.html", {
        "grouped_entries": grouped_entries,
        "title": "Exam Timetable (Published Only)",
        "logo_url": logo_url,
        "now": timezone.now(),
    })

    pdf_file = HTML(string=html).write_pdf()
    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="exam_timetable.pdf"'
    return response</code></pre>

<h3>Safe Serialization for Archiving</h3>
<pre><code class="python">def safe_serialize(obj):
    """"""
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    elif isinstance(obj, (list, tuple)):
        return [safe_serialize(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: safe_serialize(v) for k, v in obj.items()}
    return obj</code></pre>

<h3>Best Practices</h3>
<ol>
<li><strong>Always archive before deletion</strong>: Keep backup of deleted data</li>
<li><strong>Validate export data</strong>: Check for missing or invalid data</li>
<li><strong>Test PDF generation</strong>: Ensure formatting is correct</li>
<li><strong>Limit CSV size</strong>: Implement pagination for large datasets</li>
<li><strong>Secure export endpoints</strong>: Restrict access to authorized users</li>
</ol>
"""

page4 = DocumentationPage.objects.create(
    title='Export and Publishing Functions',
    slug='export-and-publishing-functions',
    short_description='Functions for publishing schedules and exporting to CSV/PDF',
    content=page4_content,
    category=categories['data-export-publishing'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=15,
    version='1.0'
)
print(f"   Created: {page4.title}")

# Page 5: COD Exam Management
page5_content = """
<h2>COD Exam Management Panel</h2>
<p>Chair of Department interface for managing department-specific exam timetables.</p>

<h3>Overview</h3>
<p>The COD Exam Management panel provides department-specific features:</p>
<ol>
<li><strong>Department filtering</strong>: View only relevant department data</li>
<li><strong>Collision detection</strong>: Prevent scheduling conflicts</li>
<li><strong>AJAX CRUD operations</strong>: Real-time updates</li>
<li><strong>Slot appending</strong>: Multiple courses in same slot</li>
<li><strong>Simple interface</strong>: Easy-to-use for department staff</li>
</ol>

<h3>Main View</h3>
<pre><code class="python">@login_required
def cot_exam_timetable(request):
    departments = Department.objects.all()
    exam_entries = ExamTimetable.objects.select_related("course_allocation__department").all()

    # Get configured slots from ExamSchedulerConfig
    config = ExamSchedulerConfig.objects.first()
    slots = []
    if config:
        start = datetime.combine(timezone.now().date(), config.start_time)
        end = datetime.combine(timezone.now().date(), config.end_time)
        delta = timedelta(hours=config.slot_size)
        while start < end:
            slot_end = start + delta
            slots.append(f"{start.time().strftime('%H:%M')} - {slot_end.time().strftime('%H:%M')}")
            start = slot_end

    return render(request, "cot_exam_timetable.html", {
        "departments": departments,
        "exam_entries": exam_entries,
        "slots": slots
    })</code></pre>

<h3>AJAX CRUD API</h3>
<pre><code class="python">@login_required
@require_POST
def cot_exam_api(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    action = data.get("action")

    # --- CREATE ---
    if action == "create":
        latest_allocation = CourseAllocation.objects.filter(submitted_to_tt=True).first()
        if not latest_allocation:
            return JsonResponse({"error": "No course allocation found"}, status=400)

        venue = data.get("venue")
        date_str = data.get("date")
        slot_str = data.get("slot")

        if not (venue and date_str and slot_str):
            return JsonResponse({"error": "Missing required fields"}, status=400)

        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        day_name = date_obj.strftime("%A")
        start_str, end_str = [s.strip() for s in slot_str.split("-")]
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()

        # --- Collision Detection ---
        overlap_exists = ExamTimetable.objects.filter(
            venue=venue,
            date=date_obj,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).exists()

        if overlap_exists and not data.get("force"):
            return JsonResponse({
                "warning": True,
                "message": f"Collision detected: {venue} already booked for this slot. Proceed?"
            })

        # --- Create or Append Entry ---
        existing_entry = ExamTimetable.objects.filter(
            course_allocation=latest_allocation,
            venue=venue,
            date=date_obj,
            start_time=start_time,
            end_time=end_time
        ).first()

        if existing_entry:
            # Append course instead of overwrite
            existing_entry.venue += f", {venue}"
            existing_entry.save()
            return JsonResponse({"success": True, "message": "Slot updated (appended)"})
        else:
            entry = ExamTimetable.objects.create(
                course_allocation=latest_allocation,
                venue=venue,
                day=day_name,
                date=date_obj,
                start_time=start_time,
                end_time=end_time
            )
            return JsonResponse({"success": True, "id": entry.id})

    # --- UPDATE ---
    elif action == "update":
        entry = get_object_or_404(ExamTimetable, id=data.get("id"))
        date_obj = datetime.strptime(data.get("date"), "%Y-%m-%d").date()
        slot_str = data.get("slot")
        start_str, end_str = [s.strip() for s in slot_str.split("-")]
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()

        # Collision check for update
        conflict = ExamTimetable.objects.filter(
            venue=data.get("venue"),
            date=date_obj,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).exclude(id=entry.id).exists()

        if conflict and not data.get("force"):
            return JsonResponse({
                "warning": True,
                "message": f"Conflict detected at {data.get('venue')} on {date_obj}. Proceed?"
            })

        entry.venue = data.get("venue")
        entry.date = date_obj
        entry.day = date_obj.strftime("%A")
        entry.start_time = start_time
        entry.end_time = end_time
        entry.save()
        return JsonResponse({"success": True})

    # --- DELETE ---
    elif action == "delete":
        entry = get_object_or_404(ExamTimetable, id=data.get("id"))
        entry.delete()
        return JsonResponse({"success": True})

    # --- LIST ---
    elif action == "list":
        dept_id = data.get("department")
        qs = ExamTimetable.objects.select_related("course_allocation__department")
        if dept_id:
            qs = qs.filter(course_allocation__department_id=dept_id)

        results = [
            {
                "id": e.id,
                "course": e.course_allocation.course_code,
                "department": e.course_allocation.department.name,
                "venue": e.venue,
                "day": e.day,
                "date": str(e.date),
                "time": f"{e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')}"
            }
            for e in qs
        ]
        return JsonResponse({"success": True, "entries": results})

    return JsonResponse({"error": "Unknown action"}, status=400)</code></pre>

<h3>Collision Detection Logic</h3>
<pre><code class="python">def time_overlaps(start_a, end_a, start_b, end_b):
    if isinstance(start_a, str): start_a = datetime.strptime(start_a, "%H:%M").time()
    if isinstance(end_a, str): end_a = datetime.strptime(end_a, "%H:%M").time()
    if isinstance(start_b, str): start_b = datetime.strptime(start_b, "%H:%M").time()
    if isinstance(end_b, str): end_b = datetime.strptime(end_b, "%H:%M").time()
    return (start_a < end_b) and (start_b < end_a)</code></pre>

<h3>Slot Appending Feature</h3>
<p>When multiple courses share the same venue and time, append to existing entry:</p>
<pre><code class="python">if existing_entry:
    # Append course instead of overwrite
    existing_entry.venue += f", {venue}"
    existing_entry.save()
    return JsonResponse({"success": True, "message": "Slot updated (appended)"})</code></pre>

<h3>Use Cases</h3>
<h4>Department Exam Planning</h4>
<ol>
<li>Filter by department to see relevant exams</li>
<li>Add new exams with collision warnings</li>
<li>Update existing exams as needed</li>
<li>Delete incorrect entries</li>
</ol>

<h4>Conflict Resolution</h4>
<ol>
<li>System detects venue/time conflicts</li>
<li>User can choose to proceed or cancel</li>
<li>Multiple courses can share slots (appending)</li>
<li>Real-time feedback on operations</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Use force parameter carefully</strong>: Override conflicts only when necessary</li>
<li><strong>Regular department reviews</strong>: Departments should review their exams weekly</li>
<li><strong>Communication</strong>: Inform affected parties of schedule changes</li>
<li><strong>Backup before bulk changes</strong>: Archive before major updates</li>
<li><strong>Training</strong>: Train department staff on using the interface</li>
</ol>
"""

page5 = DocumentationPage.objects.create(
    title='COD Exam Management Panel',
    slug='cod-exam-management-panel',
    short_description='Department-specific exam timetable management interface',
    content=page5_content,
    category=categories['cod-exam-management'],
    page_type='guide',
    difficulty='beginner',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=12,
    version='1.0'
)
print(f"   Created: {page5.title}")

# ============================================================
# 4. CREATE SECTIONS WITH CODE EXAMPLES
# ============================================================

print("\n4. CREATING SECTIONS WITH DETAILED CODE EXAMPLES...")

# Section 1: Main Auto-Scheduler Function
section1 = DocumentationSection.objects.create(
    page=page2,
    title='Main Auto-Scheduler Function',
    content='''
<h3>Complete Auto-Scheduler Implementation</h3>
<p>Main function that orchestrates the entire scheduling process.</p>
''',
    order=1,
    is_active=True,
    slug='main-auto-scheduler-function'
)
print(f"   Created section: {section1.title}")

# Code Example 1: Main Auto-Scheduler
code1 = CodeExample.objects.create(
    section=section1,
    title='run_optimized_autoscheduler_thread - Full Code',
    code='''def run_optimized_autoscheduler_thread():
    """
    Thread-safe version of the scheduler that updates progress.
    
    Returns:
        dict: Scheduling results with status, message, and counts
    """
    try:
        # Clear previous data efficiently
        with transaction.atomic():
            ExamTempTimetable.objects.all().delete()
            MergedCourseGroup.objects.all().delete()
            SharedVenueExamGroup.objects.all().delete()
        
        config = ExamSchedulerConfig.objects.first()
        if not config:
            return {
                'status': 'error', 
                'message': 'No ExamSchedulerConfig found.',
                'scheduled_count': 0,
                'remaining_count': 0
            }
        
        # Initialize optimized state
        state = OptimizedSchedulerState(config)
        
        if not state.date_range:
            return {
                'status': 'error',
                'message': 'No valid exam dates (check config / excluded days).',
                'scheduled_count': 0,
                'remaining_count': 0
            }
        
        # Get all courses
        all_courses = list(CourseAllocation.objects.all().select_related("lecturer", "program"))
        total_courses = len(all_courses)
        
        if total_courses == 0:
            return {
                'status': 'completed',
                'message': 'No courses found to schedule.',
                'scheduled_count': 0,
                'remaining_count': 0
            }
        
        # Update progress
        from TT_APP.progress_tracking_autosheduler import update_progress
        update_progress(5, f'Found {total_courses} courses to schedule', 0, total_courses)
        
        # Process in batches
        BATCH_SIZE = min(50, max(20, total_courses // 10))
        scheduled_course_ids = set()
        all_unallocated = []
        total_processed = 0
        total_batches = (total_courses + BATCH_SIZE - 1) // BATCH_SIZE
        
        for batch_num, i in enumerate(range(0, total_courses, BATCH_SIZE), 1):
            batch = all_courses[i:i + BATCH_SIZE]
            batch_courses = [c for c in batch if c.id not in scheduled_course_ids]
            
            if not batch_courses:
                continue
            
            # Update progress
            progress = min(95, int((i + len(batch)) / total_courses * 100))
            batch_info = f"Processing batch {batch_num}/{total_batches} ({len(batch_courses)} courses)"
            update_progress(
                progress, 
                f"Processing course batch {batch_num} of {total_batches}",
                len(scheduled_course_ids),
                total_courses - len(scheduled_course_ids),
                batch_info,
                batch_num,
                total_batches
            )
            
            success_count, unallocated = process_course_batch(
                batch_courses, state, scheduled_course_ids, batch_num, total_batches
            )
            
            total_processed += success_count
            all_unallocated.extend(unallocated)
            
            # Small delay to prevent server overload
            time.sleep(0.5)
        
        # Final rescue pass
        final_rescue_count = 0
        if all_unallocated:
            update_progress(98, f"Rescue pass: {len(all_unallocated)} courses remaining", 
                          len(scheduled_course_ids), len(all_unallocated))
            
            rescue_unallocated = []
            for course in all_unallocated:
                if course.id in scheduled_course_ids:
                    continue
                    
                if aggressive_single_course_placement(course, state, scheduled_course_ids):
                    final_rescue_count += 1
                else:
                    rescue_unallocated.append(course)
            
            all_unallocated = rescue_unallocated
        
        # Final results
        total_scheduled = len(scheduled_course_ids)
        
        if all_unallocated:
            return {
                'status': 'completed',
                'message': f'Scheduled {total_scheduled}/{total_courses} courses. {len(all_unallocated)} remain unscheduled.',
                'scheduled_count': total_scheduled,
                'remaining_count': len(all_unallocated)
            }
        else:
            return {
                'status': 'completed',
                'message': f'Successfully scheduled all {total_scheduled} courses! (Rescued {final_rescue_count} in final pass)',
                'scheduled_count': total_scheduled,
                'remaining_count': 0
            }
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Scheduler error: {error_details}")
        
        return {
            'status': 'error',
            'message': f'Scheduling failed: {str(e)}',
            'scheduled_count': 0,
            'remaining_count': total_courses if 'total_courses' in locals() else 0
        }''',
    language='python',
    order=1,
    description='Complete implementation of the main auto-scheduler function'
)

# Section 2: Aggressive Placement for Rescue
section2 = DocumentationSection.objects.create(
    page=page2,
    title='Aggressive Placement Algorithm',
    content='''
<h3>Rescue Mode Placement Algorithm</h3>
<p>Aggressive placement strategy for courses that couldn''t be scheduled normally.</p>
''',
    order=2,
    is_active=True,
    slug='aggressive-placement-algorithm'
)
print(f"   Created section: {section2.title}")

# Code Example 2: Aggressive Placement
code2 = CodeExample.objects.create(
    section=section2,
    title='aggressive_single_course_placement - Full Code',
    code='''def aggressive_single_course_placement(course, state, scheduled_course_ids):
    """
    Aggressive placement strategy for rescue pass - tries all possible combinations
    with relaxed constraints.
    
    Args:
        course (CourseAllocation): Course to schedule aggressively
        state (OptimizedSchedulerState): Current scheduler state
        scheduled_course_ids (set): Set of already scheduled course IDs
    
    Returns:
        bool: True if course was successfully scheduled, False otherwise
    """
    needed = getattr(course, "number_of_students", 0) or 1
    lect_id = getattr(course.lecturer, "id", None)
    prog_id = getattr(course.program, "id", None)
    program_year = get_program_year(course)
    
    # Try all date/slot combinations with relaxed constraints
    available_dates = state.date_range.copy()
    random.shuffle(available_dates)  # Randomize date order
    
    for date_obj, day_name in available_dates:
        all_slots = state.morning_slots + state.afternoon_slots
        random.shuffle(all_slots)  # Randomize slot order
        
        for slot_start, slot_end in all_slots:
            # Relaxed conflict checking in rescue mode
            if (lect_id and (date_obj, slot_start) in state.lecturer_busy.get(lect_id, set())):
                continue
            if (prog_id and (date_obj, slot_start) in state.program_busy.get(prog_id, set())):
                continue
            
            # Try all venue combinations with relaxed capacity
            available_venues = []
            for venue in state.venues:
                # Relaxed: only check if venue has any capacity at all
                if state.is_venue_available(venue.id, date_obj, slot_start, 1):
                    available_venues.append((venue, state.venue_effcap[venue.id]))
            
            # Try single room (relaxed capacity check - use raw capacity)
            for venue, capacity in available_venues:
                # Use raw capacity instead of effective capacity in rescue mode
                raw_capacity = getattr(venue, "capacity", 0)
                if raw_capacity >= needed:
                    if place_course_in_room_with_program_year(
                        course, venue, date_obj, slot_start, slot_end, state, scheduled_course_ids, program_year
                    ):
                        return True
            
            # Try room combination with relaxed constraints
            combined = greedy_combine_rooms(available_venues, needed)
            if combined:
                if place_course_in_combined_rooms_with_program_year(
                    course, combined, date_obj, slot_start, slot_end, state, scheduled_course_ids, program_year
                ):
                    return True
            
            # Even more relaxed: try splitting across any available rooms
            if len(available_venues) > 0:
                # Just take the first few rooms that have any capacity
                potential_rooms = [v for v, _ in available_venues[:3]]  # Try first 3 rooms
                if place_course_in_combined_rooms_with_program_year(
                    course, potential_rooms, date_obj, slot_start, slot_end, state, scheduled_course_ids, program_year
                ):
                    return True
    
    return False''',
    language='python',
    order=1,
    description='Aggressive placement algorithm for rescue operations'
)

# Section 3: Shared Venue Group API
section3 = DocumentationSection.objects.create(
    page=page3,
    title='Shared Venue Group API',
    content='''
<h3>CRUD API for Shared Venue Groups</h3>
<p>Complete API for managing shared venue exam groups.</p>
''',
    order=3,
    is_active=True,
    slug='shared-venue-group-api'
)
print(f"   Created section: {section3.title}")

# Code Example 3: Shared Venue API
code3 = CodeExample.objects.create(
    section=section3,
    title='shared_venue_group_api - Full Code',
    code='''@login_required
@group_required("Director Timetable", "Timetable Admins")
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def shared_venue_group_api(request):
    """
    Handles CRUD (Create, Read, Update, Delete) for SharedVenueExamGroup
    All through one endpoint using HTTP method dispatch.
    """

    if request.method == "GET":
        # Fetch published only
        groups = (
            SharedVenueExamGroup.objects.filter(published=True)
            .select_related("venue")
            .prefetch_related("course_allocations")
            .order_by("date", "start_time")
        )

        data = [
            {
                "id": g.id,
                "venue": g.venue.code,
                "date": g.date.strftime("%Y-%m-%d"),
                "day": g.day,
                "start_time": g.start_time.strftime("%H:%M"),
                "end_time": g.end_time.strftime("%H:%M"),
                "total_students": g.total_students,
                "courses": [
                    {
                        "id": c.id,
                        "course_code": c.course_code,
                        "lecturer": str(c.lecturer),
                    }
                    for c in g.course_allocations.all()
                ],
            }
            for g in groups
        ]
        return JsonResponse({"status": "success", "groups": data})

    elif request.method == "POST":
        # Create new group
        try:
            data = json.loads(request.body)
            venue_code = data.get("venue")
            date = data.get("date")
            start_time = data.get("start_time")
            end_time = data.get("end_time")
            course_ids = data.get("course_ids", [])

            venue = Venue.objects.get(code=venue_code)
            day = datetime.strptime(date, "%Y-%m-%d").strftime("%A")

            group = SharedVenueExamGroup.objects.create(
                venue=venue,
                date=date,
                day=day,
                start_time=start_time,
                end_time=end_time,
                published=True,  # Auto-publish on creation
            )
            group.course_allocations.set(course_ids)
            group.update_total_students()

            return JsonResponse({"status": "success", "message": "Shared group created successfully"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)})

    elif request.method == "PUT":
        # Update
        try:
            data = json.loads(request.body)
            group = SharedVenueExamGroup.objects.get(id=data["id"])
            if "venue" in data:
                group.venue = Venue.objects.get(code=data["venue"])
            if "date" in data:
                group.date = data["date"]
                group.day = datetime.strptime(group.date, "%Y-%m-%d").strftime("%A")
            if "start_time" in data:
                group.start_time = data["start_time"]
            if "end_time" in data:
                group.end_time = data["end_time"]
            if "course_ids" in data:
                group.course_allocations.set(data["course_ids"])
            group.save()
            group.update_total_students()

            return JsonResponse({"status": "success", "message": "Shared group updated successfully"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)})

    elif request.method == "DELETE":
        # Delete
        try:
            data = json.loads(request.body)
            SharedVenueExamGroup.objects.get(id=data["id"]).delete()
            return JsonResponse({"status": "success", "message": "Shared group deleted successfully"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)})''',
    language='python',
    order=1,
    description='Complete CRUD API for shared venue exam groups'
)

# ============================================================
# 5. ASSIGN TAGS TO PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO PAGES...")

# Define tag assignments
tag_assignments = {
    page1: ['exam-scheduling', 'auto-scheduler', 'time-slots', 'api-endpoints'],
    page2: ['exam-scheduling', 'auto-scheduler', 'conflict-detection', 'batch-processing'],
    page3: ['exam-scheduling', 'venue-management', 'data-models', 'api-endpoints'],
    page4: ['pdf-export', 'csv-export', 'data-models'],
    page5: ['cod-panel', 'exam-scheduling', 'conflict-detection'],
}

for page, tag_slugs in tag_assignments.items():
    for tag_slug in tag_slugs:
        if tag_slug in tags or tag_slug in existing_tags:
            tag = tags.get(tag_slug) or existing_tags.get(tag_slug)
            PageTag.objects.get_or_create(
                page=page,
                tag=tag
            )
    print(f"   Assigned {len(tag_slugs)} tags to: {page.title}")

# ============================================================
# 6. CREATE INTERNAL LINKS
# ============================================================

print("\n6. CREATING INTERNAL LINKS BETWEEN PAGES...")

# Define internal links
internal_links = [
    (page1, page2, "See scheduling algorithms that use this configuration"),
    (page1, page3, "Access administration panel with this configuration"),
    (page2, page3, "View scheduled exams in administration panel"),
    (page2, page4, "Export scheduled exams to PDF/CSV"),
    (page3, page4, "Publish and export timetable data"),
    (page3, page5, "COD view of department exams"),
    (page4, page5, "COD can view exported timetables"),
]

for from_page, to_page, description in internal_links:
    InternalLink.objects.get_or_create(
        from_page=from_page,
        to_page=to_page,
        defaults={'description': description}
    )
    print(f"   Linked: {from_page.title} → {to_page.title}")

# ============================================================
# 7. SUMMARY
# ============================================================

print("\n" + "=" * 80)
print("EXAM AUTO-SCHEDULER DOCUMENTATION COMPLETE")
print("=" * 80)

# Count statistics
total_pages = 5
total_sections = DocumentationSection.objects.count()
total_code_examples = CodeExample.objects.count()
total_links = InternalLink.objects.count()

print(f"""
📊 DOCUMENTATION STATISTICS:
   Total Pages Created:    {total_pages}
   Total Sections:         {total_sections}
   Total Code Examples:    {total_code_examples}
   Total Internal Links:   {total_links}

📚 NEW EXAM AUTO-SCHEDULER CONTENT:
   1. Exam Auto-Scheduler Home & Configuration
   2. Optimized Scheduler Core Algorithms
   3. Exam Timetable Administration Panel
   4. Export and Publishing Functions
   5. COD Exam Management Panel

🔧 KEY FEATURES DOCUMENTED:
   • Complete configuration management
   • Intelligent scheduling algorithms with conflict avoidance
   • Batch processing for performance
   • Program year distribution for balanced scheduling
   • Room combination algorithms
   • Administration panel with AJAX operations
   • PDF and CSV export functionality
   • COD department-specific interface
   • Collision detection and resolution

💻 TECHNICAL IMPLEMENTATION:
   • Django views with proper access control
   • Optimized database queries with select_related
   • Transaction-safe operations with atomic()
   • AJAX endpoints for real-time updates
   • WeasyPrint integration for PDF generation
   • CSV export with merged course support
   • Progress tracking for long operations
   • Error handling and recovery mechanisms

🔗 INTEGRATION POINTS:
   • Configuration → Scheduling algorithms
   • Scheduling → Administration panel
   • Administration → Export functions
   • System admin → COD department views
   • Temporary scheduling → Permanent publishing

🚀 ACCESS POINTS:
   Configuration:    /documentation/page/exam-auto-scheduler-home-configuration/
   Algorithms:       /documentation/page/optimized-scheduler-core-algorithms/
   Admin Panel:      /documentation/page/exam-timetable-administration-panel/
   Export Functions: /documentation/page/export-and-publishing-functions/
   COD Panel:        /documentation/page/cod-exam-management-panel/

🎯 OPERATIONAL WORKFLOWS:
   1. Configure exam dates, times, and venues
   2. Run auto-scheduler with batch processing
   3. Review and adjust in administration panel
   4. Publish to main timetable
   5. Export to PDF/CSV for distribution
   6. Departments review and manage their exams

✅ EXAM Auto-Scheduler documentation successfully created!
""")

print("=" * 80)
print("Documentation includes:")
print("• Complete view implementations with error handling")
print("• Scheduling algorithms with conflict avoidance")
print("• Administration interfaces with AJAX operations")
print("• Export functions for PDF and CSV formats")
print("• Department-specific management interfaces")
print("• Database optimization techniques")
print("• Security and access control implementation")
print("=" * 80)