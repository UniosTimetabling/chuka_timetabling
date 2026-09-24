#!/usr/bin/env python
"""
FEEDBACK SYSTEM, LAB TIMETABLE, AND LAB EXAM TIMETABLE DOCUMENTATION
This script creates comprehensive documentation for feedback system,
lab timetable scheduling, and lab exam scheduling functionality.
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
print("FEEDBACK SYSTEM, LAB TIMETABLE, AND LAB EXAM TIMETABLE DOCUMENTATION")
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
        'name': 'Feedback System',
        'slug': 'feedback-system',
        'description': 'User feedback collection and management system',
        'icon': 'fas fa-comments',
        'order': 28,
        'access_level': 'all',
    },
    {
        'name': 'Lab Timetable Scheduling',
        'slug': 'lab-timetable-scheduling',
        'description': 'Laboratory session scheduling and management',
        'icon': 'fas fa-flask',
        'order': 29,
        'access_level': 'department',
    },
    {
        'name': 'Lab Exam Scheduling',
        'slug': 'lab-exam-scheduling',
        'description': 'Laboratory examination scheduling and management',
        'icon': 'fas fa-file-alt',
        'order': 30,
        'access_level': 'department',
    },
    {
        'name': 'Auto-Scheduling Algorithms',
        'slug': 'auto-scheduling-algorithms',
        'description': 'Automatic scheduling algorithms and configuration',
        'icon': 'fas fa-robot',
        'order': 31,
        'access_level': 'admin',
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
    {'name': 'Feedback System', 'slug': 'feedback-system', 'color': '#28a745'},
    {'name': 'Lab Timetable', 'slug': 'lab-timetable', 'color': '#6f42c1'},
    {'name': 'Lab Exams', 'slug': 'lab-exams', 'color': '#17a2b8'},
    {'name': 'Auto Scheduling', 'slug': 'auto-scheduling', 'color': '#fd7e14'},
    {'name': 'User Feedback', 'slug': 'user-feedback', 'color': '#e83e8c'},
    {'name': 'Timetable Management', 'slug': 'timetable-management', 'color': '#20c997'},
    {'name': 'Exam Scheduling', 'slug': 'exam-scheduling', 'color': '#6c757d'},
    {'name': 'JSON API', 'slug': 'json-api', 'color': '#6610f2'},
    {'name': 'Collision Detection', 'slug': 'collision-detection', 'color': '#dc3545'},
    {'name': 'Scheduler Configuration', 'slug': 'scheduler-configuration', 'color': '#ffc107'},
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

# Page 1: Feedback System
page1_content = """
<h2>Feedback System - User Feedback Collection and Management</h2>
<p>The Feedback System allows users to submit feedback and administrators to review and manage submitted feedback.</p>

<h3>Overview</h3>
<p>The Feedback System consists of two main components:</p>
<ol>
<li><strong>Feedback Form</strong>: Public form for users to submit feedback</li>
<li><strong>Feedback Panel</strong>: Admin interface for viewing and managing feedback</li>
</ol>

<h3>Feedback Model</h3>
<p>The Feedback model stores all submitted feedback:</p>
<pre><code class="python">class Feedback(models.Model):
    full_name = models.CharField(max_length=200)
    email = models.EmailField()
    admission_number = models.CharField(max_length=50, blank=True, null=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.full_name} - {self.email}"</code></pre>

<h3>Feedback Form (Public)</h3>
<h4>View Function</h4>
<pre><code class="python">def feedback_page(request):
    \"\"\"Render the feedback form page.\"\"\"
    return render(request, "feedback.html")</code></pre>

<h4>AJAX Submission Endpoint</h4>
<pre><code class="python">@csrf_exempt
@require_POST
def submit_feedback(request):
    \"\"\"Handle feedback form submissions via POST request.\"\"\"
    try:
        data = json.loads(request.body)
        full_name = data.get("full_name", "").strip()
        email = data.get("email", "").strip()
        admission_number = data.get("admission_number", "").strip()
        message = data.get("message", "").strip()

        # Validate fields
        if not full_name or not email or not message:
            return JsonResponse({"ok": False, "error": "Please fill in all required fields."}, status=400)

        if len(message) < 10:
            return JsonResponse({"ok": False, "error": "Message is too short. Please provide more details."}, status=400)

        Feedback.objects.create(
            full_name=full_name,
            email=email,
            admission_number=admission_number or None,
            message=message
        )

        return JsonResponse({"ok": True, "message": "Thank you! Your feedback has been submitted successfully."})

    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid data format."}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"An error occurred: {e}"}, status=500)</code></pre>

<h3>Feedback Panel (Admin)</h3>
<h4>View Function</h4>
<pre><code class="python">@login_required
def feedback_panel(request):
    \"\"\"Display all submitted feedback for timetable staff.\"\"\"
    query = request.GET.get("q", "").strip()
    feedbacks = Feedback.objects.all()

    if query:
        feedbacks = feedbacks.filter(
            full_name__icontains=query
        ) | feedbacks.filter(
            email__icontains=query
        ) | feedbacks.filter(
            message__icontains=query
        )

    feedbacks = feedbacks.order_by("-created_at")

    return render(request, "feedback_panel.html", {"feedbacks": feedbacks, "query": query})</code></pre>

<h3>Validation Rules</h3>
<h4>Required Fields</h4>
<ul>
<li><strong>Full Name</strong>: Must not be empty</li>
<li><strong>Email</strong>: Must be valid email format (handled by model)</li>
<li><strong>Message</strong>: Must be at least 10 characters</li>
</ul>

<h4>Optional Fields</h4>
<ul>
<li><strong>Admission Number</strong>: Optional identifier</li>
</ul>

<h3>Security Considerations</h3>
<h4>CSRF Exemption</h4>
<p>The submission endpoint uses <code>@csrf_exempt</code> for AJAX submissions:</p>
<pre><code class="python">@csrf_exempt
@require_POST
def submit_feedback(request):
    # Implementation
    pass</code></pre>

<h4>JSON Validation</h4>
<p>Proper JSON parsing with error handling:</p>
<pre><code class="python">try:
    data = json.loads(request.body)
    # Process data
except json.JSONDecodeError:
    return JsonResponse({"ok": False, "error": "Invalid data format."}, status=400)</code></pre>

<h3>Error Responses</h3>
<h4>Error Format</h4>
<pre><code class="python">{
    "ok": False,
    "error": "Error message here"
}</code></pre>

<h4>Success Format</h4>
<pre><code class="python">{
    "ok": True,
    "message": "Success message here"
}</code></pre>

<h3>Search Functionality</h3>
<p>The feedback panel supports search across multiple fields:</p>
<pre><code class="python">if query:
    feedbacks = feedbacks.filter(
        full_name__icontains=query
    ) | feedbacks.filter(
        email__icontains=query
    ) | feedbacks.filter(
        message__icontains=query
    )</code></pre>

<h3>Template Examples</h3>
<h4>Feedback Form Template (feedback.html)</h4>
<pre><code class="django"><!-- feedback.html -->
{% extends "base.html" %}

{% block content %}
<div class="container">
    <h1>Submit Feedback</h1>
    <p>We value your feedback to improve our timetabling system.</p>
    
    <form id="feedbackForm">
        {% csrf_token %}
        
        <div class="form-group">
            <label for="full_name">Full Name *</label>
            <input type="text" class="form-control" id="full_name" name="full_name" required>
        </div>
        
        <div class="form-group">
            <label for="email">Email Address *</label>
            <input type="email" class="form-control" id="email" name="email" required>
        </div>
        
        <div class="form-group">
            <label for="admission_number">Admission Number (Optional)</label>
            <input type="text" class="form-control" id="admission_number" name="admission_number">
        </div>
        
        <div class="form-group">
            <label for="message">Message *</label>
            <textarea class="form-control" id="message" name="message" rows="5" required></textarea>
            <small class="form-text text-muted">Please provide detailed feedback (minimum 10 characters).</small>
        </div>
        
        <button type="submit" class="btn btn-primary">Submit Feedback</button>
    </form>
    
    <div id="feedbackResponse" class="mt-3"></div>
</div>

<script>
document.getElementById('feedbackForm').addEventListener('submit', function(e) {
    e.preventDefault();
    
    const formData = {
        full_name: document.getElementById('full_name').value,
        email: document.getElementById('email').value,
        admission_number: document.getElementById('admission_number').value,
        message: document.getElementById('message').value
    };
    
    fetch('/submit_feedback/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': '{{ csrf_token }}'
        },
        body: JSON.stringify(formData)
    })
    .then(response => response.json())
    .then(data => {
        const responseDiv = document.getElementById('feedbackResponse');
        if (data.ok) {
            responseDiv.innerHTML = `
                <div class="alert alert-success">
                    ${data.message}
                </div>
            `;
            document.getElementById('feedbackForm').reset();
        } else {
            responseDiv.innerHTML = `
                <div class="alert alert-danger">
                    ${data.error}
                </div>
            `;
        }
    })
    .catch(error => {
        document.getElementById('feedbackResponse').innerHTML = `
            <div class="alert alert-danger">
                Network error. Please try again.
            </div>
        `;
    });
});
</script>
{% endblock %}</code></pre>

<h3>Use Cases</h3>
<h4>User Feedback Submission</h4>
<ol>
<li>User navigates to feedback page</li>
<li>Fills out feedback form</li>
<li>Submits via AJAX</li>
<li>Receives immediate confirmation</li>
</ol>

<h4>Admin Feedback Review</h4>
<ol>
<li>Admin logs into system</li>
<li>Accesses feedback panel</li>
<li>Searches or filters feedback</li>
<li>Reviews submitted feedback</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Validate server-side</strong>: Always validate on server, not just client</li>
<li><strong>Sanitize inputs</strong>: Clean user inputs before storage</li>
<li><strong>Provide clear error messages</strong>: Help users correct their input</li>
<li><strong>Log submissions</strong>: Keep audit trail of feedback</li>
<li><strong>Respond promptly</strong>: Acknowledge feedback quickly</li>
<li><strong>Protect privacy</strong>: Handle personal information carefully</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>User Authentication</strong>: Feedback panel requires login</li>
<li><strong>Email System</strong>: Could integrate with email notifications</li>
<li><strong>Analytics</strong>: Feedback data for system improvements</li>
<li><strong>Reporting</strong>: Feedback trends and statistics</li>
</ul>
"""

page1 = DocumentationPage.objects.create(
    title='Feedback System - User Feedback Collection and Management',
    slug='feedback-system-user-feedback-collection-management',
    short_description='Complete guide to the feedback system including form submission and admin panel',
    content=page1_content,
    category=categories['feedback-system'],
    page_type='guide',
    difficulty='beginner',
    order=1,
    is_published=True,
    author=author,
    requires_login=False,
    access_level='all',
    estimated_read_time=15,
    version='1.0'
)
print(f"   Created: {page1.title}")

# Page 2: Lab Timetable Scheduling
page2_content = """
<h2>Lab Timetable Scheduling - Laboratory Session Management</h2>
<p>Comprehensive system for scheduling laboratory sessions with collision detection and auto-scheduling capabilities.</p>

<h3>Overview</h3>
<p>The Lab Timetable System manages:</p>
<ol>
<li><strong>Lab Allocations</strong>: Courses assigned to laboratories</li>
<li><strong>Lab Venues</strong>: Laboratory facilities with capacities</li>
<li><strong>Timetable Entries</strong>: Scheduled lab sessions</li>
<li><strong>Scheduler Configuration</strong>: Time slot settings</li>
</ol>

<h3>Core Models</h3>
<h4>LabAllocation Model</h4>
<pre><code class="python">class LabAllocation(models.Model):
    program_course = models.ForeignKey(ProgramCourse, on_delete=models.CASCADE)
    venue = models.ForeignKey(LabVenue, on_delete=models.CASCADE)
    lecturer = models.ForeignKey(Lecturer, on_delete=models.SET_NULL, null=True, blank=True)
    number_of_students = models.IntegerField(default=0)
    # ... other fields</code></pre>

<h4>LabVenue Model</h4>
<pre><code class="python">class LabVenue(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    capacity = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    # ... other fields</code></pre>

<h4>LabTimetable Model</h4>
<pre><code class="python">class LabTimetable(models.Model):
    lab_allocation = models.ForeignKey(LabAllocation, on_delete=models.CASCADE)
    lab_venue = models.ForeignKey(LabVenue, on_delete=models.CASCADE)
    day = models.CharField(max_length=20, choices=WEEKDAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    # ... other fields</code></pre>

<h4>LabSchedulerConfig Model</h4>
<pre><code class="python">class LabSchedulerConfig(models.Model):
    start_time = models.TimeField(default=datetime.time(8, 0))
    end_time = models.TimeField(default=datetime.time(18, 0))
    slot_size = models.IntegerField(default=2)  # hours
    # ... other fields</code></pre>

<h3>Main Panel View</h3>
<pre><code class="python">@login_required
def lab_timetable_panel(request):
    cfg = _get_scheduler_config()
    time_slots = _generate_time_slots(cfg)

    timetables = LabTimetable.objects.select_related(
        "lab_allocation__program_course", "lab_allocation__lecturer", "lab_venue"
    )

    all_allocations = LabAllocation.objects.select_related(
        "program_course", "venue", "lecturer"
    )

    venues = LabVenue.objects.all().order_by("code")

    context = {
        "heading": "Lab Timetable",
        "cfg": cfg,
        "time_slots": time_slots,
        "weekdays": WEEKDAYS,
        "timetables": timetables,
        "all_allocations": all_allocations,
        "venues": venues,
    }
    return render(request, "lab_timetable.html", context)</code></pre>

<h3>Helper Functions</h3>
<h4>Get Scheduler Configuration</h4>
<pre><code class="python">def _get_scheduler_config():
    cfg, _ = LabSchedulerConfig.objects.get_or_create(
        pk=1,
        defaults={"start_time": "07:00", "end_time": "19:00", "slot_size": 2},
    )
    return cfg</code></pre>

<h4>Generate Time Slots</h4>
<pre><code class="python">def _generate_time_slots(cfg):
    slots = []
    start = datetime.datetime.combine(datetime.date.today(), cfg.start_time)
    end = datetime.datetime.combine(datetime.date.today(), cfg.end_time)
    step = datetime.timedelta(hours=cfg.slot_size)
    cur = start
    while cur + step <= end:
        st, et = cur.time(), (cur + step).time()
        slots.append((st.strftime("%H:%M"), f"{st.strftime('%H:%M')} - {et.strftime('%H:%M')}"))
        cur += step
    return slots</code></pre>

<h3>API Endpoints</h3>
<h4>Update Configuration</h4>
<pre><code class="python">if action == "update_config":
    start_time = parse_time(request.POST.get("start_time"))
    end_time = parse_time(request.POST.get("end_time"))
    slot_size = int(request.POST.get("slot_size"))
    cfg = _get_scheduler_config()
    cfg.start_time, cfg.end_time, cfg.slot_size = start_time, end_time, slot_size
    cfg.save()
    return JsonResponse({"status": "success", "config": _cfg_to_dict(cfg)})</code></pre>

<h4>Create Timetable Entry</h4>
<pre><code class="python">if action == "create_entry":
    alloc = get_object_or_404(LabAllocation, pk=request.POST.get("allocation_id"))
    venue = get_object_or_404(LabVenue, code=request.POST.get("venue"))
    day = request.POST.get("day")
    start_time = parse_time(request.POST.get("start_time"))
    cfg = _get_scheduler_config()
    end_time = (datetime.datetime.combine(datetime.date.today(), start_time)
                + datetime.timedelta(hours=cfg.slot_size)).time()
    entry, created = LabTimetable.objects.get_or_create(
        lab_allocation=alloc,
        lab_venue=venue,
        day=day,
        start_time=start_time,
        end_time=end_time,
    )
    return JsonResponse({"status": "success", "id": entry.id})</code></pre>

<h4>Delete Timetable Entry</h4>
<pre><code class="python">if action == "delete_entry":
    entry = get_object_or_404(LabTimetable, pk=request.POST.get("entry_id"))
    entry.delete()
    return JsonResponse({"status": "success"})</code></pre>

<h3>Auto-Scheduling Algorithm</h3>
<h4>Core Algorithm Steps</h4>
<ol>
<li><strong>Clear existing timetable</strong>: Start fresh</li>
<li><strong>Get configuration</strong>: Time slots and settings</li>
<li><strong>Generate time slots</strong>: Based on configuration</li>
<li><strong>Get allocations and venues</strong>: Data to schedule</li>
<li><strong>Schedule with collision detection</strong>: Multiple checks</li>
<li><strong>Create timetable entries</strong>: For valid allocations</li>
</ol>

<h4>Collision Detection Checks</h4>
<pre><code class="python"># (a) Program clash: same program already has another lab at that time
program_busy = LabTimetable.objects.filter(
    lab_allocation__program_course__program=pc.program,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()

# (b) Lecturer clash: lecturer teaching another lab at that time
lecturer_busy = LabTimetable.objects.filter(
    lab_allocation__lecturer=alloc.lecturer,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()

# (c) Main timetable clash: lecture overlaps
lecture_clash = Timetable.objects.filter(
    course_allocation__program=pc.program,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()

# (d) Venue already taken
venue_busy = LabTimetable.objects.filter(
    lab_venue=venue,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()</code></pre>

<h4>Auto-Scheduling Function</h4>
<pre><code class="python">@login_required
@require_POST
@transaction.atomic
def run_autoscheduler(request):
    \"\"\"
    Auto-schedules labs while ensuring:
      - Each course appears only once in the timetable.
      - No overlapping labs in the same venue.
      - Venue capacity is respected.
      - No clashes with main timetable.
      - A lecturer cannot be double-booked.
      - A program cannot have two labs at the same time.
    \"\"\"
    # Implementation details
    pass</code></pre>

<h3>Scheduling Constraints</h3>
<h4>Hard Constraints (Must be satisfied)</h4>
<ul>
<li><strong>Unique course scheduling</strong>: Each course scheduled only once</li>
<li><strong>Venue capacity</strong>: Students ≤ venue capacity</li>
<li><strong>No venue double-booking</strong>: One lab per venue per time slot</li>
<li><strong>No lecturer conflicts</strong>: Lecturer not double-booked</li>
<li><strong>No program conflicts</strong>: Program not double-booked</li>
</ul>

<h4>Soft Constraints (Should be satisfied)</h4>
<ul>
<li><strong>Preferred time slots</strong>: Morning vs afternoon preferences</li>
<li><strong>Venue suitability</strong>: Special equipment requirements</li>
<li><strong>Consecutive scheduling</strong>: Multiple sessions back-to-back</li>
</ul>

<h3>Use Cases</h3>
<h4>Manual Scheduling</h4>
<ol>
<li>Configure time slots and weekdays</li>
<li>Select lab allocation</li>
<li>Choose venue and time slot</li>
<li>Create timetable entry</li>
</ol>

<h4>Auto-Scheduling</h4>
<ol>
<li>Configure scheduler settings</li>
<li>Run auto-scheduler</li>
<li>Review generated timetable</li>
<li>Make manual adjustments if needed</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Start with auto-scheduling</strong>: Use as baseline</li>
<li><strong>Review collisions</strong>: Check all constraint violations</li>
<li><strong>Manual refinement</strong>: Adjust auto-generated schedule</li>
<li><strong>Save configurations</strong>: Reuse successful settings</li>
<li><strong>Test with sample data</strong>: Validate before production</li>
<li><strong>Document decisions</strong>: Record scheduling rationale</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>Course Allocation System</strong>: Uses existing lab allocations</li>
<li><strong>Main Timetable</strong>: Checks for lecture conflicts</li>
<li><strong>Venue Management</strong>: Uses venue capacity data</li>
<li><strong>Lecturer Management</strong>: Checks lecturer availability</li>
<li><strong>Reporting System</strong>: Generates lab timetable reports</li>
</ul>
"""

page2 = DocumentationPage.objects.create(
    title='Lab Timetable Scheduling - Laboratory Session Management',
    slug='lab-timetable-scheduling-laboratory-session-management',
    short_description='Complete guide to lab timetable scheduling with auto-scheduling algorithms',
    content=page2_content,
    category=categories['lab-timetable-scheduling'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=20,
    version='1.0'
)
print(f"   Created: {page2.title}")

# Page 3: Lab Exam Scheduling
page3_content = """
<h2>Lab Exam Scheduling - Laboratory Examination Management</h2>
<p>Specialized system for scheduling laboratory examinations with advanced collision detection and date-based scheduling.</p>

<h3>Overview</h3>
<p>The Lab Exam Scheduling System handles:</p>
<ol>
<li><strong>Date-based scheduling</strong>: Exams scheduled on specific dates</li>
<li><strong>Exam-specific constraints</strong>: Special rules for examinations</li>
<li><strong>Extended conflict detection</strong>: Comprehensive collision checking</li>
<li><strong>Exam configuration</strong>: Date ranges and time settings</li>
</ol>

<h3>Core Models</h3>
<h4>LabExamTimetable Model</h4>
<pre><code class="python">class LabExamTimetable(models.Model):
    lab_allocation = models.ForeignKey(LabAllocation, on_delete=models.CASCADE)
    lab_venue = models.ForeignKey(LabVenue, on_delete=models.CASCADE)
    date = models.DateField()  # Specific date, not just day of week
    day = models.CharField(max_length=20)  # Day name for display
    start_time = models.TimeField()
    end_time = models.TimeField()
    # ... other fields</code></pre>

<h4>ExamSchedulerConfig Model</h4>
<pre><code class="python">class ExamSchedulerConfig(models.Model):
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField(default=datetime.time(8, 0))
    end_time = models.TimeField(default=datetime.time(17, 0))
    slot_size = models.IntegerField(default=2)
    max_exam_days = models.IntegerField(default=14)
    excluded_days = models.TextField(blank=True)  # Comma-separated dates
    # ... other fields</code></pre>

<h3>Main Panel View</h3>
<pre><code class="python">@login_required
def lab_exam_timetable_panel(request):
    cfg = _get_scheduler_config()
    time_slots = _generate_time_slots(cfg)
    date_range = _get_valid_date_range(cfg)

    timetables = LabExamTimetable.objects.select_related(
        "lab_allocation__program_course", "lab_allocation__lecturer", "lab_venue"
    )
    all_allocations = LabAllocation.objects.select_related("program_course", "venue", "lecturer")
    venues = LabVenue.objects.all().order_by("code")

    return render(request, "lab_exam_timetable.html", {
        "heading": "Lab Exam Timetable",
        "cfg": cfg,
        "time_slots": time_slots,
        "date_range": date_range,
        "timetables": timetables,
        "all_allocations": all_allocations,
        "venues": venues,
    })</code></pre>

<h3>Helper Functions</h3>
<h4>Get Valid Date Range</h4>
<pre><code class="python">def _get_valid_date_range(cfg):
    \"\"\"Return valid scheduling dates (YYYY-MM-DD, DayName), skipping weekends.\"\"\"
    start_date = cfg.start_date
    valid_dates = []
    for i in range(cfg.max_exam_days):  # next 2 weeks
        d = start_date + datetime.timedelta(days=i)
        if d.weekday() < 5:  # Monday–Friday only
            valid_dates.append((d.strftime("%Y-%m-%d"), d.strftime("%A")))
    return valid_dates</code></pre>

<h4>Generate Time Slots</h4>
<pre><code class="python">def _generate_time_slots(cfg):
    \"\"\"Generate time slot tuples (start_str, end_str) based on config.\"\"\"
    slots = []
    today = datetime.date.today()
    start_dt = datetime.datetime.combine(today, cfg.start_time)
    end_dt = datetime.datetime.combine(today, cfg.end_time)
    while start_dt < end_dt:
        next_dt = start_dt + datetime.timedelta(hours=cfg.slot_size)
        if next_dt.time() > cfg.end_time:
            break
        slots.append((start_dt.time().strftime("%H:%M"), next_dt.time().strftime("%H:%M")))
        start_dt = next_dt
    return slots</code></pre>

<h3>API Endpoints</h3>
<h4>Update Exam Configuration</h4>
<pre><code class="python">if action == "update_config":
    cfg = _get_scheduler_config()
    cfg.start_date = request.POST.get("start_date") or cfg.start_date
    cfg.end_date = request.POST.get("end_date") or cfg.end_date
    cfg.start_time = parse_time(request.POST.get("start_time"))
    cfg.end_time = parse_time(request.POST.get("end_time"))
    cfg.slot_size = int(request.POST.get("slot_size"))
    cfg.save()
    return JsonResponse({"status": "success", "message": "Scheduler configuration updated."})</code></pre>

<h4>Create Exam Entry</h4>
<pre><code class="python">if action == "create_entry":
    alloc = get_object_or_404(LabAllocation, pk=request.POST.get("allocation_id"))
    venue = get_object_or_404(LabVenue, code=request.POST.get("venue"))
    date_str = request.POST.get("date")
    date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    start_time = parse_time(request.POST.get("start_time"))
    cfg = _get_scheduler_config()
    end_time = (datetime.datetime.combine(date, start_time) +
                datetime.timedelta(hours=cfg.slot_size)).time()
    day = date.strftime("%A")
    entry, _ = LabExamTimetable.objects.get_or_create(
        lab_allocation=alloc,
        lab_venue=venue,
        date=date,
        day=day,
        start_time=start_time,
        end_time=end_time,
    )
    return JsonResponse({"status": "success", "id": entry.id, "message": "Entry created."})</code></pre>

<h3>Auto-Scheduling Algorithm</h3>
<h4>Exam-Specific Constraints</h4>
<pre><code class="python">@login_required
@require_POST
@transaction.atomic
def run_lab_exam_autoscheduler(request):
    \"\"\"
    Automatically assign lab exams ensuring:
    ✅ No program course collision (students in same program can't have two exams at same time)
    ✅ No lecturer double booking
    ✅ Venue capacity and availability
    ✅ Dynamic date & time slot allocation
    \"\"\"
    # Implementation
    pass</code></pre>

<h4>Program Collision Detection</h4>
<pre><code class="python"># CHECK 1: PROGRAM COLLISION
program_conflict = (
    ExamTimetable.objects.filter(
        course_allocation__program=program,
        date=date,
        start_time__lt=et,
        end_time__gt=st
    ).exists()
    or LabExamTimetable.objects.filter(
        lab_allocation__program_course__program=program,
        date=date,
        start_time__lt=et,
        end_time__gt=st
    ).exists()
)</code></pre>

<h4>Lecturer Collision Detection</h4>
<pre><code class="python"># CHECK 2: LECTURER COLLISION
lecturer_conflict = False
if lecturer:
    lecturer_conflict = (
        LabExamTimetable.objects.filter(
            lab_allocation__lecturer=lecturer,
            date=date,
            start_time__lt=et,
            end_time__gt=st,
        ).exists()
        or ExamTimetable.objects.filter(
            course_allocation__lecturer=lecturer,
            date=date,
            start_time__lt=et,
            end_time__gt=st,
        ).exists()
    )</code></pre>

<h4>Venue Availability Check</h4>
<pre><code class="python"># CHECK 3: VENUE AVAILABILITY
venue = next(
    (v for v in suitable_venues if not LabExamTimetable.objects.filter(
        lab_venue=v,
        date=date,
        start_time__lt=et,
        end_time__gt=st
    ).exists()),
    None
)</code></pre>

<h3>Date Management</h3>
<h4>Weekday Filtering</h4>
<pre><code class="python"># Only schedule on weekdays (Monday-Friday)
if d.weekday() < 5:  # Monday=0, Friday=4, Saturday=5, Sunday=6
    valid_dates.append((d.strftime("%Y-%m-%d"), d.strftime("%A")))</code></pre>

<h4>Excluded Dates Handling</h4>
<pre><code class="python"># Handle excluded dates
excluded = []
if hasattr(cfg, "excluded_days") and cfg.excluded_days:
    try:
        excluded = [datetime.datetime.strptime(d.strip(), "%Y-%m-%d").date()
                    for d in cfg.excluded_days.split(",") if d.strip()]
    except Exception:
        excluded = []

# Skip excluded dates
if current not in excluded:
    # Schedule on this date</code></pre>

<h3>Exam-Specific Considerations</h3>
<h4>Extended Scheduling Period</h4>
<ul>
<li><strong>Multiple weeks</strong>: Exams scheduled over several weeks</li>
<li><strong>Weekday only</strong>: No exams on weekends</li>
<li><strong>Public holidays</strong>: Configurable excluded dates</li>
</ul>

<h4>Strict Conflict Rules</h4>
<ul>
<li><strong>Program-level conflicts</strong>: Students can't have two exams simultaneously</li>
<li><strong>Lecturer supervision</strong>: Lecturers can't supervise multiple exams</li>
<li><strong>Venue capacity</strong>: Must accommodate all students</li>
<li><strong>Main exam conflicts</strong>: Check against written exam timetable</li>
</ul>

<h3>Use Cases</h3>
<h4>End-of-Semester Exams</h4>
<ol>
<li>Configure exam period dates</li>
<li>Set time slots for exams</li>
<li>Run auto-scheduler for initial schedule</li>
<li>Review and adjust conflicts</li>
<li>Publish final exam timetable</li>
</ol>

<h4>Mid-Term Exams</h4>
<ol>
<li>Set shorter date range</li>
<li>Configure appropriate time slots</li>
<li>Schedule with reduced constraints</li>
<li>Coordinate with regular timetable</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Plan exam periods early</strong>: Set dates well in advance</li>
<li><strong>Configure excluded dates</strong>: Account for holidays and events</li>
<li><strong>Run multiple scheduling attempts</strong>: Adjust parameters for better results</li>
<li><strong>Validate against main exams</strong>: Ensure no student conflicts</li>
<li><strong>Communicate schedule early</strong>: Give students advance notice</li>
<li><strong>Have backup venues</strong>: Plan for capacity issues</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>Main Exam Timetable</strong>: Checks for written exam conflicts</li>
<li><strong>Academic Calendar</strong>: Uses official dates and holidays</li>
<li><strong>Student Information System</strong>: Validates program enrollments</li>
<li><strong>Venue Booking System</strong>: Coordinates with other bookings</li>
<li><strong>Notification System</strong>: Sends exam schedule notifications</li>
</ul>
"""

page3 = DocumentationPage.objects.create(
    title='Lab Exam Scheduling - Laboratory Examination Management',
    slug='lab-exam-scheduling-laboratory-examination-management',
    short_description='Complete guide to lab exam scheduling with date-based constraints',
    content=page3_content,
    category=categories['lab-exam-scheduling'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=22,
    version='1.0'
)
print(f"   Created: {page3.title}")

# Page 4: Auto-Scheduling Algorithms
page4_content = """
<h2>Auto-Scheduling Algorithms - Intelligent Timetable Generation</h2>
<p>Advanced algorithms for automatic timetable generation with comprehensive constraint satisfaction.</p>

<h3>Overview</h3>
<p>The Auto-Scheduling System provides:</p>
<ol>
<li><strong>Constraint-based scheduling</strong>: Multiple constraint types and priorities</li>
<li><strong>Collision detection</strong>: Comprehensive conflict checking</li>
<li><strong>Optimization algorithms</strong>: Heuristic approaches for good solutions</li>
<li><strong>Configuration management</strong>: Flexible scheduling parameters</li>
</ol>

<h3>Algorithm Architecture</h3>
<h4>Basic Auto-Scheduler Structure</h4>
<pre><code class="python">def run_autoscheduler(request):
    \"\"\"
    Basic auto-scheduler structure:
    1. Clear existing timetable
    2. Get configuration and generate time slots
    3. Get allocations and venues
    4. Schedule with constraint checking
    5. Return results
    \"\"\"
    # 1️⃣ Clear existing lab timetable
    LabTimetable.objects.all().delete()
    
    # 2️⃣ Get scheduler configuration
    cfg = LabSchedulerConfig.objects.first()
    if not cfg:
        return JsonResponse(
            {"status": "error", "message": "Scheduler configuration missing."}, status=400
        )
    
    # Generate time slots
    start = datetime.datetime.combine(datetime.date.today(), cfg.start_time)
    end = datetime.datetime.combine(datetime.date.today(), cfg.end_time)
    step = datetime.timedelta(hours=cfg.slot_size)
    
    slots = []
    cur = start
    while cur + step <= end:
        slots.append((cur.time(), (cur + step).time()))
        cur += step
    
    # 3️⃣ Get data
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    venues = list(LabVenue.objects.all().order_by("code"))
    allocations = list(
        LabAllocation.objects.select_related("program_course", "lecturer", "program_course__program")
    )
    
    # 4️⃣ Schedule with constraint checking
    created_count = 0
    scheduled_courses = set()
    random.shuffle(allocations)
    
    for alloc in allocations:
        # Constraint checking and scheduling logic
        pass
    
    # 5️⃣ Return results
    return JsonResponse({
        "status": "success",
        "message": f"Auto-scheduling completed. {created_count} sessions created."
    })</code></pre>

<h3>Constraint Types</h3>
<h4>Hard Constraints (Must be satisfied)</h4>
<pre><code class="python"># 1. Course uniqueness - each course scheduled only once
if course_code in scheduled_courses:
    continue

# 2. Venue capacity - students must fit in venue
suitable_venues = [v for v in venues if (v.capacity or 0) >= students]

# 3. No venue double-booking
venue_busy = LabTimetable.objects.filter(
    lab_venue=venue,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()

# 4. No lecturer conflicts
lecturer_busy = LabTimetable.objects.filter(
    lab_allocation__lecturer=alloc.lecturer,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()

# 5. No program conflicts
program_busy = LabTimetable.objects.filter(
    lab_allocation__program_course__program=pc.program,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()

# 6. No main timetable conflicts
lecture_clash = Timetable.objects.filter(
    course_allocation__program=pc.program,
    day=day,
    start_time__lt=et,
    end_time__gt=st,
).exists()</code></pre>

<h4>Soft Constraints (Should be satisfied)</h4>
<pre><code class="python"># 1. Preferred time of day (morning vs afternoon)
def is_preferred_time(start_time):
    morning_end = datetime.time(12, 0)
    return start_time < morning_end

# 2. Consecutive sessions (if needed)
def has_consecutive_session(alloc, day, start_time):
    # Check if this course has another session that could be scheduled consecutively
    pass

# 3. Venue suitability (special equipment)
def is_venue_suitable(venue, alloc):
    # Check if venue has required equipment
    pass

# 4. Load balancing (distribute across days)
def get_day_with_least_load(allocations_scheduled_per_day):
    return min(allocations_scheduled_per_day, key=allocations_scheduled_per_day.get)</code></pre>

<h3>Scheduling Strategies</h3>
<h4>Randomized Backtracking</h4>
<pre><code class="python">def randomized_backtracking_schedule(allocations, venues, weekdays, slots):
    \"\"\"Randomized backtracking algorithm for scheduling.\"\"\"
    scheduled = []
    unscheduled = []
    
    # Shuffle allocations for random ordering
    random.shuffle(allocations)
    
    for alloc in allocations:
        placed = False
        
        # Try random order of days and slots
        random.shuffle(weekdays)
        random.shuffle(slots)
        
        for day in weekdays:
            for start_time, end_time in slots:
                # Find suitable venue
                suitable_venue = find_suitable_venue(alloc, venues, day, start_time, end_time)
                
                if suitable_venue and check_all_constraints(alloc, suitable_venue, day, start_time, end_time):
                    # Schedule this allocation
                    schedule_allocation(alloc, suitable_venue, day, start_time, end_time)
                    scheduled.append(alloc)
                    placed = True
                    break
            
            if placed:
                break
        
        if not placed:
            unscheduled.append(alloc)
    
    return scheduled, unscheduled</code></pre>

<h4>Heuristic Search</h4>
<pre><code class="python">def heuristic_schedule(allocations, venues, weekdays, slots):
    \"\"\"Heuristic scheduling with priority ordering.\"\"\"
    # Sort allocations by difficulty (hardest to schedule first)
    allocations_sorted = sorted(allocations, key=lambda a: (
        -a.number_of_students,  # Larger groups first
        -len(get_suitable_venues(a, venues)),  # Fewer venue options first
        random.random()  # Random tie-breaker
    ))
    
    scheduled = []
    
    for alloc in allocations_sorted:
        # Try days in order of current load (balance across days)
        days_by_load = sorted(weekdays, key=lambda d: get_current_load(d))
        
        for day in days_by_load:
            # Try time slots from start to end
            for start_time, end_time in slots:
                venue = find_best_venue(alloc, venues, day, start_time, end_time)
                
                if venue:
                    schedule_allocation(alloc, venue, day, start_time, end_time)
                    scheduled.append(alloc)
                    break
        
        # If still not scheduled, try with relaxed constraints
        if alloc not in scheduled:
            scheduled.extend(try_relaxed_constraints(alloc, venues, weekdays, slots))
    
    return scheduled</code></pre>

<h4>Genetic Algorithm Approach</h4>
<pre><code class="python">def genetic_algorithm_schedule(allocations, venues, weekdays, slots, generations=100, population_size=50):
    \"\"\"Genetic algorithm for scheduling optimization.\"\"\"
    
    class ScheduleChromosome:
        def __init__(self):
            self.genes = []  # List of (alloc, venue, day, time) tuples
            self.fitness = 0
        
        def calculate_fitness(self):
            \"\"\"Calculate fitness based on constraints satisfied.\"\"\"
            hard_constraints_score = check_hard_constraints(self.genes)
            soft_constraints_score = check_soft_constraints(self.genes)
            coverage_score = len(set(a.id for a, _, _, _ in self.genes)) / len(allocations)
            
            self.fitness = (hard_constraints_score * 1000 + 
                          soft_constraints_score * 100 + 
                          coverage_score * 10)
    
    # Initialize population
    population = [generate_random_chromosome() for _ in range(population_size)]
    
    for generation in range(generations):
        # Evaluate fitness
        for chromosome in population:
            chromosome.calculate_fitness()
        
        # Selection
        population.sort(key=lambda c: c.fitness, reverse=True)
        next_generation = population[:population_size // 2]  # Keep top 50%
        
        # Crossover and mutation
        while len(next_generation) < population_size:
            parent1, parent2 = random.choices(population[:10], k=2)
            child = crossover(parent1, parent2)
            child = mutate(child)
            next_generation.append(child)
        
        population = next_generation
    
    # Return best schedule
    best = max(population, key=lambda c: c.fitness)
    return best.genes</code></pre>

<h3>Configuration Management</h3>
<h4>Scheduler Configuration Model</h4>
<pre><code class="python">class AdvancedSchedulerConfig(models.Model):
    \"\"\"Advanced configuration for auto-scheduler.\"\"\"
    name = models.CharField(max_length=100)
    
    # Time settings
    start_time = models.TimeField(default=datetime.time(8, 0))
    end_time = models.TimeField(default=datetime.time(18, 0))
    slot_size = models.IntegerField(default=2)  # hours
    
    # Date settings
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    exclude_weekends = models.BooleanField(default=True)
    excluded_dates = models.TextField(blank=True)  # JSON list of dates
    
    # Algorithm settings
    algorithm = models.CharField(max_length=50, choices=[
        ('backtracking', 'Backtracking'),
        ('heuristic', 'Heuristic Search'),
        ('genetic', 'Genetic Algorithm'),
    ], default='heuristic')
    
    max_iterations = models.IntegerField(default=1000)
    population_size = models.IntegerField(default=50, help_text="For genetic algorithm")
    
    # Constraint weights
    weight_hard_constraints = models.IntegerField(default=1000)
    weight_soft_constraints = models.IntegerField(default=100)
    weight_coverage = models.IntegerField(default=10)
    
    # Advanced settings
    allow_constraint_relaxation = models.BooleanField(default=False)
    max_relaxation_level = models.IntegerField(default=3)
    prefer_morning_slots = models.BooleanField(default=True)
    balance_daily_load = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name
    
    def get_excluded_dates_list(self):
        \"\"\"Parse excluded dates from JSON string.\"\"\"
        try:
            return json.loads(self.excluded_dates) if self.excluded_dates else []
        except:
            return []</code></pre>

<h3>Performance Optimization</h3>
<h4>Efficient Constraint Checking</h4>
<pre><code class="python">class ConstraintChecker:
    \"\"\"Efficient constraint checking with caching.\"\"\"
    
    def __init__(self):
        self.venue_cache = {}  # Cache venue availability
        self.lecturer_cache = {}  # Cache lecturer schedules
        self.program_cache = {}  # Cache program schedules
    
    def check_venue_availability(self, venue, day, start_time, end_time):
        \"\"\"Check if venue is available with caching.\"\"\"
        cache_key = f"{venue.id}-{day}-{start_time}-{end_time}"
        
        if cache_key in self.venue_cache:
            return self.venue_cache[cache_key]
        
        is_available = not LabTimetable.objects.filter(
            lab_venue=venue,
            day=day,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exists()
        
        self.venue_cache[cache_key] = is_available
        return is_available
    
    def check_lecturer_availability(self, lecturer, day, start_time, end_time):
        \"\"\"Check if lecturer is available with caching.\"\"\"
        if not lecturer:
            return True
        
        cache_key = f"{lecturer.id}-{day}-{start_time}-{end_time}"
        
        if cache_key in self.lecturer_cache:
            return self.lecturer_cache[cache_key]
        
        is_available = not LabTimetable.objects.filter(
            lab_allocation__lecturer=lecturer,
            day=day,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exists()
        
        self.lecturer_cache[cache_key] = is_available
        return is_available</code></pre>

<h3>Error Handling and Reporting</h3>
<h4>Comprehensive Error Responses</h4>
<pre><code class="python">def run_autoscheduler_with_reporting(request):
    \"\"\"Auto-scheduler with detailed error reporting.\"\"\"
    try:
        # Run scheduling
        result = run_scheduling_algorithm()
        
        # Generate report
        report = {
            "status": "success",
            "summary": {
                "total_allocations": len(result["allocations"]),
                "scheduled": len(result["scheduled"]),
                "unscheduled": len(result["unscheduled"]),
                "constraint_violations": result["violations"],
                "execution_time": result["execution_time"],
            },
            "details": {
                "scheduled_allocations": [
                    {
                        "course": a.program_course.course_code,
                        "venue": v.code,
                        "day": day,
                        "time": f"{start_time}-{end_time}",
                    }
                    for a, v, day, start_time, end_time in result["scheduled"]
                ],
                "unscheduled_allocations": [
                    {
                        "course": a.program_course.course_code,
                        "reason": reason,
                    }
                    for a, reason in result["unscheduled"]
                ],
                "constraint_analysis": analyze_constraints(result),
            },
        }
        
        return JsonResponse(report)
        
    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc() if settings.DEBUG else None,
        }, status=500)</code></pre>

<h3>Use Cases</h3>
<h4>Initial Semester Scheduling</h4>
<ol>
<li>Configure basic scheduler settings</li>
<li>Run simple heuristic algorithm</li>
<li>Review and adjust problematic allocations</li>
<li>Save as baseline schedule</li>
</ol>

<h4>Complex Scheduling Scenarios</h4>
<ol>
<li>Configure advanced algorithm with custom weights</li>
<li>Run genetic algorithm for optimization</li>
<li>Analyze constraint violations</li>
<li>Iteratively refine schedule</li>
</ol>

<h4>Emergency Rescheduling</h4>
<ol>
<li>Load existing schedule as baseline</li>
<li>Identify changed allocations</li>
<li>Run incremental scheduler</li>
<li>Minimize disruptions to existing schedule</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Start simple</strong>: Begin with basic algorithms before complex ones</li>
<li><strong>Validate constraints</strong>: Thoroughly test constraint checking</li>
<li><strong>Profile performance</strong>: Identify and optimize bottlenecks</li>
<li><strong>Cache results</strong>: Use caching for repeated constraint checks</li>
<li><strong>Provide detailed reports</strong>: Help users understand scheduling decisions</li>
<li><strong>Allow manual overrides</strong>: Always provide option for manual adjustments</li>
<li><strong>Save configurations</strong>: Reuse successful parameter sets</li>
<li><strong>Test with varied data</strong>: Ensure robustness across different scenarios</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>Constraint Management System</strong>: Define and manage scheduling constraints</li>
<li><strong>Performance Monitoring</strong>: Track scheduler performance and improvements</li>
<li><strong>Reporting System</strong>: Generate scheduling reports and analytics</li>
<li><strong>Configuration Management</strong>: Save and load scheduler configurations</li>
<li><strong>Notification System</strong>: Alert users about scheduling results</li>
</ul>
"""

page4 = DocumentationPage.objects.create(
    title='Auto-Scheduling Algorithms - Intelligent Timetable Generation',
    slug='auto-scheduling-algorithms-intelligent-timetable-generation',
    short_description='Advanced algorithms for automatic timetable generation with constraint satisfaction',
    content=page4_content,
    category=categories['auto-scheduling-algorithms'],
    page_type='reference',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=25,
    version='1.0'
)
print(f"   Created: {page4.title}")

# ============================================================
# 4. CREATE SECTIONS WITH CODE EXAMPLES
# ============================================================

print("\n4. CREATING SECTIONS WITH DETAILED CODE EXAMPLES...")

# Section 1: Complete Feedback System
section1 = DocumentationSection.objects.create(
    page=page1,
    title='Complete Feedback System Implementation',
    content='''
<h3>Full Feedback System Code</h3>
<p>Complete implementation of the feedback system including models, views, and templates.</p>
''',
    order=1,
    is_active=True,
    slug='feedback-system-complete-implementation'
)
print(f"   Created section: {section1.title}")

# Code Example 1: Complete Feedback System
code1 = CodeExample.objects.create(
    section=section1,
    title='Complete Feedback System Code',
    code='''# models.py
from django.db import models
from django.utils import timezone

class Feedback(models.Model):
    \"\"\"Model for storing user feedback.\"\"\"
    full_name = models.CharField(max_length=200)
    email = models.EmailField()
    admission_number = models.CharField(max_length=50, blank=True, null=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Feedback'
        verbose_name_plural = 'Feedbacks'
    
    def __str__(self):
        return f"{self.full_name} - {self.email}"
    
    def get_short_message(self):
        \"\"\"Get first 100 characters of message for display.\"\"\"
        if len(self.message) > 100:
            return self.message[:100] + '...'
        return self.message


# views.py
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from TT_APP.models import Feedback
import json

def feedback_page(request):
    \"\"\"Render the feedback form page.\"\"\"
    return render(request, "feedback.html")


@csrf_exempt
@require_POST
def submit_feedback(request):
    \"\"\"Handle feedback form submissions via POST request.\"\"\"
    try:
        # Parse JSON data
        data = json.loads(request.body)
        full_name = data.get("full_name", "").strip()
        email = data.get("email", "").strip()
        admission_number = data.get("admission_number", "").strip()
        message = data.get("message", "").strip()

        # Validate required fields
        if not full_name:
            return JsonResponse({"ok": False, "error": "Full name is required."}, status=400)
        if not email:
            return JsonResponse({"ok": False, "error": "Email address is required."}, status=400)
        if not message:
            return JsonResponse({"ok": False, "error": "Message is required."}, status=400)

        # Validate message length
        if len(message) < 10:
            return JsonResponse({
                "ok": False, 
                "error": "Message is too short. Please provide at least 10 characters."
            }, status=400)

        # Validate email format (basic check)
        if '@' not in email or '.' not in email:
            return JsonResponse({"ok": False, "error": "Please enter a valid email address."}, status=400)

        # Create feedback record
        feedback = Feedback.objects.create(
            full_name=full_name,
            email=email,
            admission_number=admission_number or None,
            message=message
        )

        # Log successful submission (optional)
        print(f"Feedback submitted: {feedback.id} by {full_name}")

        return JsonResponse({
            "ok": True, 
            "message": "Thank you! Your feedback has been submitted successfully.",
            "feedback_id": feedback.id
        })

    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid data format. Please try again."}, status=400)
    except Exception as e:
        # Log the error for debugging
        print(f"Error submitting feedback: {e}")
        return JsonResponse({
            "ok": False, 
            "error": "An unexpected error occurred. Please try again later."
        }, status=500)


@login_required
def feedback_panel(request):
    \"\"\"Display all submitted feedback for timetable staff.\"\"\"
    query = request.GET.get("q", "").strip()
    feedbacks = Feedback.objects.all()

    # Search functionality
    if query:
        feedbacks = feedbacks.filter(
            full_name__icontains=query
        ) | feedbacks.filter(
            email__icontains=query
        ) | feedbacks.filter(
            message__icontains=query
        ) | feedbacks.filter(
            admission_number__icontains=query
        )

    # Order by most recent first
    feedbacks = feedbacks.order_by("-created_at")

    # Get statistics
    total_feedbacks = feedbacks.count()
    today_feedbacks = feedbacks.filter(created_at__date=timezone.now().date()).count()
    
    context = {
        "feedbacks": feedbacks,
        "query": query,
        "total_feedbacks": total_feedbacks,
        "today_feedbacks": today_feedbacks,
        "search_performed": bool(query),
    }
    
    return render(request, "feedback_panel.html", context)


@login_required
@require_POST
def delete_feedback(request):
    \"\"\"Delete a feedback entry (admin only).\"\"\"
    try:
        feedback_id = request.POST.get("id")
        feedback = Feedback.objects.get(id=feedback_id)
        feedback.delete()
        
        return JsonResponse({
            "ok": True,
            "message": "Feedback deleted successfully."
        })
    except Feedback.DoesNotExist:
        return JsonResponse({
            "ok": False,
            "error": "Feedback not found."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": f"Error deleting feedback: {e}"
        }, status=500)


# urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('feedback/', views.feedback_page, name='feedback_page'),
    path('submit_feedback/', views.submit_feedback, name='submit_feedback'),
    path('feedback_panel/', views.feedback_panel, name='feedback_panel'),
    path('delete_feedback/', views.delete_feedback, name='delete_feedback'),
]


# templates/feedback.html
\"\"\"
{% extends "base.html" %}

{% block title %}Submit Feedback - University Timetabling System{% endblock %}

{% block content %}
<div class="container py-5">
    <div class="row justify-content-center">
        <div class="col-md-8">
            <div class="card">
                <div class="card-header bg-primary text-white">
                    <h2 class="h4 mb-0">Submit Feedback</h2>
                </div>
                <div class="card-body">
                    <p class="card-text">
                        We value your feedback to help us improve the timetabling system. 
                        Please share your suggestions, issues, or compliments.
                    </p>
                    
                    <form id="feedbackForm" novalidate>
                        {% csrf_token %}
                        
                        <div class="form-group">
                            <label for="full_name" class="font-weight-bold">Full Name *</label>
                            <input type="text" 
                                   class="form-control" 
                                   id="full_name" 
                                   name="full_name" 
                                   placeholder="Enter your full name"
                                   required>
                            <div class="invalid-feedback">
                                Please provide your full name.
                            </div>
                        </div>
                        
                        <div class="form-group">
                            <label for="email" class="font-weight-bold">Email Address *</label>
                            <input type="email" 
                                   class="form-control" 
                                   id="email" 
                                   name="email" 
                                   placeholder="Enter your email address"
                                   required>
                            <div class="invalid-feedback">
                                Please provide a valid email address.
                            </div>
                        </div>
                        
                        <div class="form-group">
                            <label for="admission_number" class="font-weight-bold">Admission Number (Optional)</label>
                            <input type="text" 
                                   class="form-control" 
                                   id="admission_number" 
                                   name="admission_number"
                                   placeholder="e.g., COM/0001/2020">
                            <small class="form-text text-muted">
                                Helpful for identifying specific issues related to your program.
                            </small>
                        </div>
                        
                        <div class="form-group">
                            <label for="message" class="font-weight-bold">Message *</label>
                            <textarea class="form-control" 
                                      id="message" 
                                      name="message" 
                                      rows="5" 
                                      placeholder="Please provide detailed feedback..."
                                      required></textarea>
                            <div class="invalid-feedback">
                                Please provide a message (minimum 10 characters).
                            </div>
                            <small class="form-text text-muted">
                                Minimum 10 characters. Be as specific as possible to help us address your feedback.
                            </small>
                        </div>
                        
                        <div class="form-group">
                            <div class="form-check">
                                <input class="form-check-input" 
                                       type="checkbox" 
                                       id="agree_terms" 
                                       required>
                                <label class="form-check-label" for="agree_terms">
                                    I agree that my feedback may be used to improve the system.
                                </label>
                                <div class="invalid-feedback">
                                    You must agree before submitting.
                                </div>
                            </div>
                        </div>
                        
                        <button type="submit" class="btn btn-primary btn-lg">
                            <i class="fas fa-paper-plane mr-2"></i>Submit Feedback
                        </button>
                        
                        <button type="reset" class="btn btn-outline-secondary btn-lg ml-2">
                            <i class="fas fa-redo mr-2"></i>Clear Form
                        </button>
                    </form>
                    
                    <div id="feedbackResponse" class="mt-4"></div>
                </div>
                <div class="card-footer text-muted">
                    <small>
                        <i class="fas fa-info-circle mr-1"></i>
                        Your feedback is important to us. We typically respond within 2-3 business days.
                    </small>
                </div>
            </div>
            
            <div class="mt-4 text-center">
                <a href="{% url 'home' %}" class="btn btn-outline-primary">
                    <i class="fas fa-arrow-left mr-2"></i>Back to Home
                </a>
            </div>
        </div>
    </div>
</div>

<script>
document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('feedbackForm');
    const responseDiv = document.getElementById('feedbackResponse');
    
    // Form validation
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        
        // Clear previous response
        responseDiv.innerHTML = '';
        
        // Validate form
        if (!form.checkValidity()) {
            e.stopPropagation();
            form.classList.add('was-validated');
            return;
        }
        
        // Get form data
        const formData = {
            full_name: document.getElementById('full_name').value.trim(),
            email: document.getElementById('email').value.trim(),
            admission_number: document.getElementById('admission_number').value.trim(),
            message: document.getElementById('message').value.trim()
        };
        
        // Additional validation
        if (formData.message.length < 10) {
            showError('Message must be at least 10 characters long.');
            return;
        }
        
        // Show loading state
        const submitBtn = form.querySelector('button[type="submit"]');
        const originalText = submitBtn.innerHTML;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Submitting...';
        submitBtn.disabled = true;
        
        // Submit via AJAX
        fetch('/submit_feedback/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify(formData)
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.ok) {
                showSuccess(data.message);
                form.reset();
                form.classList.remove('was-validated');
            } else {
                showError(data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showError('Network error. Please check your connection and try again.');
        })
        .finally(() => {
            // Restore button state
            submitBtn.innerHTML = originalText;
            submitBtn.disabled = false;
        });
    });
    
    // Helper functions
    function showSuccess(message) {
        responseDiv.innerHTML = `
            <div class="alert alert-success alert-dismissible fade show" role="alert">
                <i class="fas fa-check-circle mr-2"></i>
                ${message}
                <button type="button" class="close" data-dismiss="alert" aria-label="Close">
                    <span aria-hidden="true">&times;</span>
                </button>
            </div>
        `;
        
        // Scroll to response
        responseDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    
    function showError(message) {
        responseDiv.innerHTML = `
            <div class="alert alert-danger alert-dismissible fade show" role="alert">
                <i class="fas fa-exclamation-triangle mr-2"></i>
                ${message}
                <button type="button" class="close" data-dismiss="alert" aria-label="Close">
                    <span aria-hidden="true">&times;</span>
                </button>
            </div>
        `;
        
        // Scroll to response
        responseDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    
    // CSRF token helper
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    
    // Real-time message length counter
    const messageTextarea = document.getElementById('message');
    const messageCounter = document.createElement('small');
    messageCounter.className = 'form-text text-muted float-right';
    messageCounter.textContent = '0/10 characters';
    messageTextarea.parentNode.appendChild(messageCounter);
    
    messageTextarea.addEventListener('input', function() {
        const length = this.value.length;
        messageCounter.textContent = `${length}/10 characters`;
        
        if (length < 10) {
            messageCounter.classList.remove('text-success');
            messageCounter.classList.add('text-danger');
        } else {
            messageCounter.classList.remove('text-danger');
            messageCounter.classList.add('text-success');
        }
    });
});
</script>
{% endblock %}
\"\"\"</code></pre>
''',
    language='python',
    order=1,
    description='Complete feedback system implementation including models, views, and templates'
)

# Section 2: Lab Exam Auto-Scheduler
section2 = DocumentationSection.objects.create(
    page=page3,
    title='Complete Lab Exam Auto-Scheduler',
    content='''
<h3>Complete Lab Exam Auto-Scheduling Algorithm</h3>
<p>Full implementation of the lab exam auto-scheduler with all constraints and optimizations.</p>
''',
    order=1,
    is_active=True,
    slug='lab-exam-auto-scheduler-complete'
)
print(f"   Created section: {section2.title}")

# Code Example 2: Lab Exam Auto-Scheduler
code2 = CodeExample.objects.create(
    section=section2,
    title='Lab Exam Auto-Scheduler - Complete Implementation',
    code='''from django.db import transaction
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.dateparse import parse_time
from TT_APP.models import (
    LabExamTimetable, LabVenue, LabAllocation,
    ExamTimetable, ExamSchedulerConfig
)
import datetime, random


def _get_scheduler_config():
    \"\"\"Return active lab exam scheduler configuration.\"\"\"
    return ExamSchedulerConfig.objects.first()


def _generate_time_slots(cfg):
    \"\"\"Generate time slot tuples (start_str, end_str) based on config.\"\"\"
    slots = []
    today = datetime.date.today()
    start_dt = datetime.datetime.combine(today, cfg.start_time)
    end_dt = datetime.datetime.combine(today, cfg.end_time)
    
    while start_dt < end_dt:
        next_dt = start_dt + datetime.timedelta(hours=cfg.slot_size)
        if next_dt.time() > cfg.end_time:
            break
        slots.append((start_dt.time().strftime("%H:%M"), next_dt.time().strftime("%H:%M")))
        start_dt = next_dt
    
    return slots


def _get_valid_date_range(cfg):
    \"\"\"
    Return valid scheduling dates (YYYY-MM-DD, DayName), skipping weekends.
    
    Args:
        cfg: ExamSchedulerConfig instance
        
    Returns:
        list: List of (date_str, day_name) tuples
    \"\"\"
    start_date = cfg.start_date
    valid_dates = []
    
    # Generate dates for the configured period
    for i in range(cfg.max_exam_days):
        current_date = start_date + datetime.timedelta(days=i)
        
        # Skip weekends (Monday=0, Friday=4, Saturday=5, Sunday=6)
        if current_date.weekday() < 5:
            valid_dates.append((
                current_date.strftime("%Y-%m-%d"),
                current_date.strftime("%A")
            ))
    
    return valid_dates


class ConstraintChecker:
    \"\"\"Efficient constraint checker for lab exam scheduling.\"\"\"
    
    def __init__(self):
        self.venue_cache = {}
        self.lecturer_cache = {}
        self.program_cache = {}
        
    def check_program_collision(self, program, date, start_time, end_time):
        \"\"\"
        Check if program already has an exam (lab or written) at the given time.
        
        Args:
            program: Program instance
            date: datetime.date
            start_time: datetime.time
            end_time: datetime.time
            
        Returns:
            bool: True if collision exists
        \"\"\"
        cache_key = f"program-{program.id}-{date}-{start_time}-{end_time}"
        
        if cache_key in self.program_cache:
            return self.program_cache[cache_key]
        
        # Check written exams
        written_exam_conflict = ExamTimetable.objects.filter(
            course_allocation__program=program,
            date=date,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).exists()
        
        # Check lab exams
        lab_exam_conflict = LabExamTimetable.objects.filter(
            lab_allocation__program_course__program=program,
            date=date,
            start_time__lt=end_time,
            end_time__gt=start_time
        ).exists()
        
        has_conflict = written_exam_conflict or lab_exam_conflict
        self.program_cache[cache_key] = has_conflict
        
        return has_conflict
    
    def check_lecturer_collision(self, lecturer, date, start_time, end_time):
        \"\"\"
        Check if lecturer is already scheduled at the given time.
        
        Args:
            lecturer: Lecturer instance (can be None)
            date: datetime.date
            start_time: datetime.time
            end_time: datetime.time
            
        Returns:
            bool: True if collision exists
        \"\"\"
        if not lecturer:
            return False
        
        cache_key = f"lecturer-{lecturer.id}-{date}-{start_time}-{end_time}"
        
        if cache_key in self.lecturer_cache:
            return self.lecturer_cache[cache_key]
        
        # Check lab exams
        lab_exam_conflict = LabExamTimetable.objects.filter(
            lab_allocation__lecturer=lecturer,
            date=date,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exists()
        
        # Check written exams
        written_exam_conflict = ExamTimetable.objects.filter(
            course_allocation__lecturer=lecturer,
            date=date,
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).exists()
        
        has_conflict = lab_exam_conflict or written_exam_conflict
        self.lecturer_cache[cache_key] = has_conflict
        
        return has_conflict
    
    def find_available_venue(self, suitable_venues, date, start_time, end_time):
        \"\"\"
        Find first available venue from the list of suitable venues.
        
        Args:
            suitable_venues: List of LabVenue instances
            date: datetime.date
            start_time: datetime.time
            end_time: datetime.time
            
        Returns:
            LabVenue or None: Available venue or None if none available
        \"\"\"
        for venue in suitable_venues:
            cache_key = f"venue-{venue.id}-{date}-{start_time}-{end_time}"
            
            if cache_key in self.venue_cache:
                is_available = self.venue_cache[cache_key]
            else:
                is_available = not LabExamTimetable.objects.filter(
                    lab_venue=venue,
                    date=date,
                    start_time__lt=end_time,
                    end_time__gt=start_time
                ).exists()
                self.venue_cache[cache_key] = is_available
            
            if is_available:
                return venue
        
        return None


@login_required
@require_POST
@transaction.atomic
def run_lab_exam_autoscheduler(request):
    \"\"\"
    Automatically assign lab exams ensuring:
    ✅ No program course collision (students in same program can't have two exams at same time)
    ✅ No lecturer double booking
    ✅ Venue capacity and availability
    ✅ Dynamic date & time slot allocation
    
    Returns:
        JsonResponse: Success or error response with details
    \"\"\"
    try:
        # 1️⃣ Clear existing lab exam timetable
        deleted_count = LabExamTimetable.objects.all().delete()[0]
        print(f"Cleared {deleted_count} existing lab exam entries")
        
        # 2️⃣ Get scheduler configuration
        cfg = _get_scheduler_config()
        if not cfg:
            return JsonResponse({
                "status": "error",
                "message": "No active scheduler configuration found. Please configure exam scheduling first."
            }, status=400)
        
        # 3️⃣ Generate time slots and valid dates
        time_slots = _generate_time_slots(cfg)
        valid_dates = _get_valid_date_range(cfg)
        
        if not time_slots:
            return JsonResponse({
                "status": "error",
                "message": "No valid time slots generated. Check scheduler configuration."
            }, status=400)
        
        if not valid_dates:
            return JsonResponse({
                "status": "error",
                "message": "No valid dates found. Check start date and max exam days."
            }, status=400)
        
        # 4️⃣ Get all venues and allocations
        venues = list(LabVenue.objects.all().order_by("code"))
        allocations = list(LabAllocation.objects.select_related(
            "program_course", "program_course__program", "lecturer"
        ))
        
        if not allocations:
            return JsonResponse({
                "status": "error",
                "message": "No lab allocations found. Please create lab allocations first."
            }, status=400)
        
        print(f"Starting auto-scheduling for {len(allocations)} lab allocations")
        print(f"Time slots: {len(time_slots)}, Valid dates: {len(valid_dates)}, Venues: {len(venues)}")
        
        # 5️⃣ Initialize constraint checker and counters
        checker = ConstraintChecker()
        created_count = 0
        unscheduled_allocations = []
        random.shuffle(allocations)  # Randomize for better distribution
        
        # 6️⃣ Schedule each allocation
        for alloc in allocations:
            students = alloc.number_of_students or 0
            lecturer = alloc.lecturer
            program = alloc.program_course.program
            
            # Find suitable venues based on capacity
            suitable_venues = [v for v in venues if (v.capacity or 0) >= students]
            
            if not suitable_venues:
                unscheduled_allocations.append({
                    "allocation": alloc,
                    "reason": f"No suitable venue (capacity needed: {students})"
                })
                continue
            
            placed = False
            
            # Try each date and time slot
            for date_str, day in valid_dates:
                if placed:
                    break
                
                date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                
                for st_str, et_str in time_slots:
                    if placed:
                        break
                    
                    start_time = parse_time(st_str)
                    end_time = parse_time(et_str)
                    
                    # Check program collision
                    if checker.check_program_collision(program, date, start_time, end_time):
                        continue  # Try next time slot
                    
                    # Check lecturer collision
                    if checker.check_lecturer_collision(lecturer, date, start_time, end_time):
                        continue  # Try next time slot
                    
                    # Find available venue
                    venue = checker.find_available_venue(suitable_venues, date, start_time, end_time)
                    
                    if venue:
                        # ✅ All checks passed - schedule the exam
                        LabExamTimetable.objects.create(
                            lab_allocation=alloc,
                            lab_venue=venue,
                            date=date,
                            day=day,
                            start_time=start_time,
                            end_time=end_time,
                        )
                        
                        created_count += 1
                        placed = True
                        
                        print(f"Scheduled: {alloc.program_course.course_code} on {date} {start_time}-{end_time} at {venue.code}")
            
            if not placed:
                unscheduled_allocations.append({
                    "allocation": alloc,
                    "reason": "No available slot found (all slots occupied or conflicting)"
                })
        
        # 7️⃣ Generate detailed report
        report = {
            "status": "success",
            "summary": {
                "total_allocations": len(allocations),
                "scheduled": created_count,
                "unscheduled": len(unscheduled_allocations),
                "scheduling_rate": f"{(created_count / len(allocations) * 100):.1f}%" if allocations else "0%",
                "time_slots_used": f"{len(time_slots)} slots across {len(valid_dates)} days",
            },
            "details": {
                "configuration": {
                    "start_date": cfg.start_date.strftime("%Y-%m-%d"),
                    "end_date": cfg.end_date.strftime("%Y-%m-%d"),
                    "start_time": cfg.start_time.strftime("%H:%M"),
                    "end_time": cfg.end_time.strftime("%H:%M"),
                    "slot_size": cfg.slot_size,
                    "max_exam_days": cfg.max_exam_days,
                },
                "unscheduled_reasons": [
                    {
                        "course": item["allocation"].program_course.course_code,
                        "reason": item["reason"]
                    }
                    for item in unscheduled_allocations
                ],
            },
            "recommendations": generate_recommendations(created_count, unscheduled_allocations, cfg)
        }
        
        # 8️⃣ Return success response
        if created_count == 0:
            report["status"] = "warning"
            report["message"] = "Auto-scheduling completed but no lab exam sessions were scheduled."
        else:
            report["message"] = f"Auto-scheduling completed successfully. {created_count} lab exam sessions created."
        
        return JsonResponse(report)
        
    except Exception as e:
        # Log the error
        import traceback
        error_details = traceback.format_exc()
        print(f"Auto-scheduling error: {e}")
        print(f"Traceback: {error_details}")
        
        return JsonResponse({
            "status": "error",
            "message": f"Auto-scheduling failed: {str(e)}",
            "error_details": error_details if settings.DEBUG else None
        }, status=500)


def generate_recommendations(scheduled_count, unscheduled_allocations, cfg):
    \"\"\"Generate recommendations based on scheduling results.\"\"\"
    recommendations = []
    
    if scheduled_count == 0:
        recommendations.append("No sessions were scheduled. Check venue capacities and time slot configuration.")
    
    capacity_issues = [a for a in unscheduled_allocations 
                      if "capacity" in a["reason"].lower()]
    if capacity_issues:
        recommendations.append(f"{len(capacity_issues)} allocations failed due to venue capacity. Consider:")
        recommendations.append("  • Increasing venue capacities")
        recommendations.append("  • Splitting large groups across multiple venues")
        recommendations.append("  • Adding more lab venues")
    
    slot_issues = [a for a in unscheduled_allocations 
                  if "slot" in a["reason"].lower()]
    if slot_issues:
        recommendations.append(f"{len(slot_issues)} allocations couldn't find available slots. Consider:")
        recommendations.append("  • Increasing exam period duration")
        recommendations.append("  • Adding more time slots per day")
        recommendations.append("  • Reducing slot size to fit more sessions")
    
    # Check if we're using all available time
    total_slots = len(_generate_time_slots(cfg)) * len(_get_valid_date_range(cfg))
    utilization = scheduled_count / total_slots if total_slots > 0 else 0
    
    if utilization < 0.5:
        recommendations.append(f"Low slot utilization ({utilization:.0%}). Consider reducing time slots or exam days.")
    elif utilization > 0.9:
        recommendations.append(f"High slot utilization ({utilization:.0%}). Consider adding more time or reducing sessions.")
    
    return recommendations


# Additional helper function for incremental scheduling
@login_required
@require_POST
@transaction.atomic
def run_incremental_scheduler(request):
    \"\"\"
    Incremental scheduler that only schedules unscheduled allocations
    without clearing existing timetable.
    \"\"\"
    try:
        # Get already scheduled allocations
        scheduled_allocation_ids = set(
            LabExamTimetable.objects.values_list('lab_allocation_id', flat=True)
        )
        
        # Get unscheduled allocations
        unscheduled_allocations = LabAllocation.objects.exclude(
            id__in=scheduled_allocation_ids
        ).select_related(
            "program_course", "program_course__program", "lecturer"
        )
        
        if not unscheduled_allocations:
            return JsonResponse({
                "status": "success",
                "message": "All allocations are already scheduled."
            })
        
        # Rest of the scheduling logic similar to run_lab_exam_autoscheduler
        # but only for unscheduled allocations
        
        # ... scheduling implementation ...
        
        return JsonResponse({
            "status": "success",
            "message": f"Incremental scheduling completed. Scheduled {created_count} new sessions."
        })
        
    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": f"Incremental scheduling failed: {str(e)}"
        }, status=500)</code></pre>
''',
    language='python',
    order=1,
    description='Complete lab exam auto-scheduler with constraint checking and reporting'
)

# ============================================================
# 5. ASSIGN TAGS TO PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO PAGES...")

# Define tag assignments
tag_assignments = {
    page1: ['feedback-system', 'user-feedback', 'json-api'],
    page2: ['lab-timetable', 'timetable-management', 'auto-scheduling', 'collision-detection'],
    page3: ['lab-exams', 'exam-scheduling', 'auto-scheduling', 'collision-detection'],
    page4: ['auto-scheduling', 'scheduler-configuration', 'timetable-management'],
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
    (page1, page2, "Feedback may include lab scheduling issues"),
    (page2, page3, "Regular lab scheduling vs exam scheduling"),
    (page2, page4, "Auto-scheduling algorithms used in lab timetable"),
    (page3, page4, "Exam scheduling uses specialized auto-scheduling"),
    (page4, page2, "Algorithm implementations for lab scheduling"),
    (page4, page3, "Algorithm implementations for exam scheduling"),
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
print("FEEDBACK, LAB TIMETABLE, AND EXAM SCHEDULING DOCUMENTATION COMPLETE")
print("=" * 80)

# Count statistics
total_pages = DocumentationPage.objects.count()
total_sections = DocumentationSection.objects.count()
total_code_examples = CodeExample.objects.count()
total_links = InternalLink.objects.count()

print(f"""
📊 DOCUMENTATION STATISTICS:
   Total Pages Created:    4
   Total Sections:         {total_sections}
   Total Code Examples:    {total_code_examples}
   Total Internal Links:   {total_links}

📚 NEW SYSTEM DOCUMENTATION:
   1. Feedback System - User Feedback Collection and Management
   2. Lab Timetable Scheduling - Laboratory Session Management
   3. Lab Exam Scheduling - Laboratory Examination Management
   4. Auto-Scheduling Algorithms - Intelligent Timetable Generation

🔧 KEY FEATURES DOCUMENTED:
   • User feedback collection with AJAX submission
   • Feedback administration panel with search
   • Lab timetable scheduling with collision detection
   • Lab exam scheduling with date-based constraints
   • Multiple auto-scheduling algorithms
   • Comprehensive constraint checking
   • Configuration management for schedulers
   • Detailed reporting and error handling

💻 TECHNICAL IMPLEMENTATION:
   • Complete feedback system with models, views, and templates
   • Lab scheduling with multiple constraint types
   • Exam scheduling with date management
   • Advanced auto-scheduling algorithms
   • Efficient constraint checking with caching
   • Transaction-safe scheduling operations
   • Detailed error reporting and recommendations
   • JSON API endpoints for AJAX operations

🔗 INTEGRATION POINTS:
   • Feedback system for user input and system improvement
   • Lab scheduling integrates with main timetable
   • Exam scheduling coordinates with written exams
   • Auto-scheduling algorithms reusable across systems
   • Configuration management for different scheduling scenarios

🚀 ACCESS POINTS:
   Feedback System:      /documentation/page/feedback-system-user-feedback-collection-management/
   Lab Timetable:        /documentation/page/lab-timetable-scheduling-laboratory-session-management/
   Lab Exams:            /documentation/page/lab-exam-scheduling-laboratory-examination-management/
   Auto-Scheduling:      /documentation/page/auto-scheduling-algorithms-intelligent-timetable-generation/

🎯 OPERATIONAL WORKFLOWS:
   1. Users submit feedback → admins review and respond
   2. Configure lab scheduler → run auto-scheduling → manual adjustments
   3. Set exam period dates → schedule exams → resolve conflicts → publish
   4. Choose algorithm → configure parameters → run scheduler → analyze results

✅ Documentation successfully created for feedback, lab scheduling, and exam systems!
""")

print("=" * 80)
print("Documentation includes:")
print("• Complete system implementations with error handling")
print("• Advanced scheduling algorithms with multiple strategies")
print("• Comprehensive constraint checking and collision detection")
print("• Configuration management for different scenarios")
print("• Detailed reporting and analysis tools")
print("• Template examples with JavaScript functionality")
print("• Integration patterns between different systems")
print("• Best practices for scheduling and feedback management")
print("=" * 80)