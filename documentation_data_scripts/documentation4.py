#!/usr/bin/env python
"""
COD PANEL AND COURSE ALLOCATION DOCUMENTATION
This script creates comprehensive documentation for COD panel, course allocation,
and related functionality in the timetabling system.
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
print("COD PANEL AND COURSE ALLOCATION DOCUMENTATION")
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
        'name': 'COD Panel Operations',
        'slug': 'cod-panel-operations',
        'description': 'Chair of Department panel operations including course allocation management',
        'icon': 'fas fa-user-tie',
        'order': 21,
        'access_level': 'department',
    },
    {
        'name': 'Data Archiving & Cleanup',
        'slug': 'data-archiving-cleanup',
        'description': 'Archiving, restoring, and deleting course allocation data',
        'icon': 'fas fa-archive',
        'order': 22,
        'access_level': 'department',
    },
    {
        'name': 'Automated Allocation',
        'slug': 'automated-allocation',
        'description': 'Automatic course allocation and semester management',
        'icon': 'fas fa-robot',
        'order': 23,
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
    {'name': 'COD Panel', 'slug': 'cod-panel', 'color': '#28a745'},
    {'name': 'Data Archiving', 'slug': 'data-archiving', 'color': '#6f42c1'},
    {'name': 'Course Allocation', 'slug': 'course-allocation', 'color': '#17a2b8'},
    {'name': 'Auto Allocation', 'slug': 'auto-allocation', 'color': '#fd7e14'},
    {'name': 'Data Cleanup', 'slug': 'data-cleanup', 'color': '#e83e8c'},
    {'name': 'Semester Management', 'slug': 'semester-management', 'color': '#20c997'},
    {'name': 'Department Operations', 'slug': 'department-operations', 'color': '#6c757d'},
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

# Page 1: COD Panel Archive & Clear
page1_content = """
<h2>COD Panel Archive & Clear Function</h2>
<p>This function allows CODs to archive, restore, and delete course allocation data by semester.</p>

<h3>Overview</h3>
<p>The <code>cod_panel_clear</code> view provides three main operations:</p>
<ol>
<li><strong>Archive & Delete</strong>: Archive all current allocations and delete them from active table</li>
<li><strong>Restore Semester</strong>: Restore archived allocations back to active table</li>
<li><strong>Delete Archived Semester</strong>: Permanently delete archived semester data</li>
</ol>

<h3>Department Detection</h3>
<p>The system uses <code>detect_user_department()</code> to identify the COD's department:</p>
<pre><code class="python">dept = detect_user_department(user)
if not dept:
    messages.error(request, "Unable to detect your department. Please contact admin.")
    return redirect("cod_panel")</code></pre>

<h3>Data Architecture</h3>
<h4>Active Allocations</h4>
<ul>
<li><strong>CourseAllocation</strong>: Current/active allocations</li>
<li><strong>Filtered by department</strong>: Only shows COD's department data</li>
<li><strong>Includes related objects</strong>: Lecturer, program, origin department</li>
</ul>

<h4>Archived Allocations</h4>
<ul>
<li><strong>ArchivedCourseAllocation</strong>: Historical/archived allocations</li>
<li><strong>Includes metadata</strong>: archived_by, archived_at, semester</li>
<li><strong>Grouped by semester</strong>: Display organized by semester</li>
</ul>

<h3>Archive & Delete Operation</h3>
<h4>Process Flow</h4>
<ol>
<li><strong>Validate semester input</strong>: Ensure semester is provided</li>
<li><strong>Check for allocations</strong>: Verify department has allocations</li>
<li><strong>Atomic transaction</strong>: Wrap in transaction.atomic()</li>
<li><strong>Create archived copies</strong>: Copy all allocations to archived table</li>
<li><strong>Delete originals</strong>: Remove from CourseAllocation table</li>
<li><strong>Success message</strong>: Display count of archived items</li>
</ol>

<h4>Key Implementation</h4>
<pre><code class="python">with transaction.atomic():
    archived_objs = []
    for a in allocations:
        archived_objs.append(ArchivedCourseAllocation(
            department=dept,
            semester=semester,
            archived_by=user,
            # Copy all fields...
        ))
    ArchivedCourseAllocation.objects.bulk_create(archived_objs)
    count, _ = allocations.delete()</code></pre>

<h3>Restore Operation</h3>
<h4>Process Flow</h4>
<ol>
<li><strong>Validate semester input</strong>: Ensure semester is provided</li>
<li><strong>Check archived data exists</strong>: Verify semester has archived data</li>
<li><strong>Atomic transaction</strong>: Wrap in transaction.atomic()</li>
<li><strong>Duplicate prevention</strong>: Skip if allocation already exists</li>
<li><strong>Create new allocations</strong>: Copy from archived to active</li>
<li><strong>Success message</strong>: Display created/skipped counts</li>
</ol>

<h4>Duplicate Prevention</h4>
<pre><code class="python">exists = CourseAllocation.objects.filter(
    department=dept,
    course_code__iexact=a.course_code,
    program=a.program
).exists()
if exists:
    skipped += 1
    continue</code></pre>

<h3>Permanent Delete Operation</h3>
<h4>Process Flow</h4>
<ol>
<li><strong>Validate semester input</strong>: Ensure semester is provided</li>
<li><strong>Get archived records</strong>: Filter by department and semester</li>
<li><strong>Count before deletion</strong>: Store count for message</li>
<li><strong>Perform deletion</strong>: Delete all matching archived records</li>
<li><strong>Success message</strong>: Display count of deleted items</li>
</ol>

<h3>Data Grouping for Display</h3>
<p>Use <code>defaultdict</code> to group archived allocations by semester:</p>
<pre><code class="python">from collections import defaultdict
archived_by_semester = defaultdict(list)
for a in archived_qs:
    archived_by_semester[a.semester].append(a)</code></pre>

<h3>Security Considerations</h3>
<ul>
<li><strong>@login_required</strong>: Only authenticated users can access</li>
<li><strong>Department scoping</strong>: Users can only see their department data</li>
<li><strong>Transaction safety</strong>: Atomic operations prevent partial updates</li>
<li><strong>Input validation</strong>: Validate all user inputs</li>
</ul>

<h3>Error Handling</h3>
<ul>
<li><strong>Department detection failure</strong>: Redirect with error message</li>
<li><strong>Missing semester</strong>: Error message for required field</li>
<li><strong>No allocations</strong>: Warning if no data to archive</li>
<li><strong>No archived data</strong>: Warning if semester not found in archive</li>
</ul>

<h3>Use Cases</h3>
<h4>End of Semester Cleanup</h4>
<ol>
<li>Archive all current allocations with semester label (e.g., "2024S2")</li>
<li>Delete from active table to prepare for new semester</li>
<li>Keep archived data for historical reference</li>
</ol>

<h4>Semester Restoration</h4>
<ol>
<li>Restore previous semester's allocations</li>
<li>Useful for repeating similar allocations</li>
<li>Duplicate prevention ensures no conflicts</li>
</ol>

<h4>Data Cleanup</h4>
<ol>
<li>Permanently delete old archived data</li>
<li>Free up database space</li>
<li>Maintain data retention policies</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Clear semester naming</strong>: Use consistent naming (e.g., "2024S1", "Fall2024")</li>
<li><strong>Regular archiving</strong>: Archive at end of each semester</li>
<li><strong>Backup before deletion</strong>: Ensure backups before permanent deletion</li>
<li><strong>Communicate changes</strong>: Inform stakeholders before major operations</li>
<li><strong>Test restoration</strong>: Periodically test restore functionality</li>
</ol>
"""

page1 = DocumentationPage.objects.create(
    title='COD Panel Archive & Clear Operations',
    slug='cod-panel-archive-clear-operations',
    short_description='Guide to archiving, restoring, and deleting course allocation data by semester',
    content=page1_content,
    category=categories['cod-panel-operations'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=15,
    version='1.0'
)
print(f"   Created: {page1.title}")

# Page 2: Auto Allocate Courses
page2_content = """
<h2>Auto Allocate Courses Function</h2>
<p>Automatically allocate courses to lecturers based on semester selection and fair distribution rules.</p>

<h3>Overview</h3>
<p>The <code>auto_allocate_courses</code> view performs automatic course allocation with the following steps:</p>
<ol>
<li>Delete old allocations for selected semester</li>
<li>Fetch valid courses for the semester</li>
<li>Ensure semester distribution (if all are semester 1)</li>
<li>Allocate courses to lecturers with fair distribution</li>
<li>Display results</li>
</ol>

<h3>Department Detection</h3>
<p>Same department detection as other COD functions:</p>
<pre><code class="python">dept = detect_user_department(user)
if not dept:
    messages.error(request, "Unable to detect your department. Please contact admin.")
    return redirect("cod_panel")</code></pre>

<h3>Semester Selection</h3>
<p>POST request includes semester selection:</p>
<pre><code class="python">selected_semester = int(request.POST.get("semester"))
programs = Program.objects.filter(department=dept)</code></pre>

<h3>Old Allocation Cleanup</h3>
<h4>Targeted Deletion</h4>
<p>Delete only allocations for the selected semester:</p>
<pre><code class="python">old_allocations = CourseAllocation.objects.filter(
    department=dept,
    program__in=programs,
    course_code__in=ProgramCourse.objects.filter(
        program__in=programs,
        semester=selected_semester
    ).values_list("course_code", flat=True)
)
deleted_count = old_allocations.count()
old_allocations.delete()</code></pre>

<h3>Course Fetching and Semester Distribution</h3>
<h4>Fetch Valid Courses</h4>
<pre><code class="python">all_courses = ProgramCourse.objects.filter(
    program__in=programs,
    semester=selected_semester
).order_by("program__name", "course_code")</code></pre>

<h4>Semester Distribution Logic</h4>
<p>If all courses in a program are semester 1, distribute half to semester 2:</p>
<pre><code class="python">for program in programs:
    prog_courses = list(ProgramCourse.objects.filter(program=program))
    if prog_courses and all(c.semester == 1 for c in prog_courses):
        half = len(prog_courses) // 2
        for c in prog_courses[:half]:
            c.semester = 1
            c.save()
        for c in prog_courses[half:]:
            c.semester = 2
            c.save()</code></pre>

<h3>Lecturer Allocation</h3>
<h4>Lecturer Lists</h4>
<pre><code class="python">lecturers = list(Lecturer.objects.filter(department=dept))
other_lecturers = list(Lecturer.objects.exclude(department=dept))</code></pre>

<h4>Fair Distribution Algorithm</h4>
<p>Allocate courses fairly, ensuring no lecturer gets more than 6 courses:</p>
<pre><code class="python">for course in all_courses:
    allocation = CourseAllocation.objects.create(
        course_code=course.course_code,
        course_name=course.course_name,
        program=course.program,
        department=dept,
        origin_department=dept
    )

    # Auto-assign lecturers fairly
    if lecturers:
        lecturer_allocs = {lec: CourseAllocation.objects.filter(lecturer=lec).count() for lec in lecturers}
        available_lecturers = [lec for lec, cnt in lecturer_allocs.items() if cnt < 6]
        allocation.lecturer = choice(available_lecturers or lecturers)
    elif other_lecturers:
        allocation.lecturer = choice(other_lecturers)

    allocation.save()</code></pre>

<h3>Results Display</h3>
<p>Prepare allocations for display with semester information:</p>
<pre><code class="python">allocations = []
for alloc in CourseAllocation.objects.filter(department=dept).select_related("lecturer", "program"):
    course = ProgramCourse.objects.filter(program=alloc.program, course_code=alloc.course_code).first()
    allocations.append({
        "program": alloc.program.name,
        "course_code": alloc.course_code,
        "course_name": alloc.course_name,
        "semester": course.semester if course else "N/A",
        "lecturer": alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
    })</code></pre>

<h3>Success Message</h3>
<p>Informative success message with counts:</p>
<pre><code class="python">messages.success(
    request,
    f"Semester {selected_semester} courses re-allocated successfully. "
    f"{deleted_count} old allocations removed and {all_courses.count()} courses newly assigned."
)</code></pre>

<h3>Use Cases</h3>
<h4>Start of Semester Setup</h4>
<ol>
<li>Select semester (1 or 2)</li>
<li>System clears old allocations for that semester</li>
<li>Automatically assigns courses to available lecturers</li>
<li>Ensures fair distribution (max 6 courses per lecturer)</li>
</ol>

<h4>Mid-Semester Adjustments</h4>
<ol>
<li>Re-run auto allocation for adjustments</li>
<li>Maintains semester-specific focus</li>
<li>Preserves other semester allocations</li>
</ol>

<h3>Limitations and Considerations</h3>
<ul>
<li><strong>Max 6 courses</strong>: Hard-coded limit for fair distribution</li>
<li><strong>Department lecturers first</strong>: Prefers department lecturers over others</li>
<li><strong>Random selection</strong>: Uses random.choice for assignment</li>
<li><strong>Semester 1 bias</strong>: Special logic for semester 1 courses</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Review before submission</strong>: Always review auto allocations before finalizing</li>
<li><strong>Manual adjustments</strong>: Be prepared to make manual adjustments</li>
<li><strong>Communicate changes</strong>: Inform lecturers of new assignments</li>
<li><strong>Backup first</strong>: Backup data before running auto allocation</li>
<li><strong>Test with small sets</strong>: Test with subset before full department</li>
</ol>
"""

page2 = DocumentationPage.objects.create(
    title='Auto Allocate Courses Function',
    slug='auto-allocate-courses-function',
    short_description='Automatic course allocation with fair lecturer distribution',
    content=page2_content,
    category=categories['automated-allocation'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=12,
    version='1.0'
)
print(f"   Created: {page2.title}")

# Page 3: Course Allocations List
page3_content = """
<h2>Course Allocations List View</h2>
<p>Display all course allocations with department filtering and efficient data loading.</p>

<h3>Overview</h3>
<p>The <code>course_allocations_list</code> view provides a comprehensive list of all course allocations with:</p>
<ul>
<li>Eager loading of related objects for performance</li>
<li>Department filtering capability</li>
<li>Ordered display by course code and name</li>
</ul>

<h3>Function Implementation</h3>
<pre><code class="python">@login_required
def course_allocations_list(request):
    # Eager load related objects for efficiency
    allocations = CourseAllocation.objects.select_related(
        "department", "origin_department", "program", "lecturer"
    ).all().order_by("course_code", "course_name")

    departments = Department.objects.all().order_by("name")

    return render(
        request,
        "course_allocations.html",
        {
            "allocations": allocations,
            "departments": departments,
        },
    )</code></pre>

<h3>Eager Loading with select_related</h3>
<p>Use <code>select_related</code> to prevent N+1 query problem:</p>
<pre><code class="python">allocations = CourseAllocation.objects.select_related(
    "department",        # ForeignKey to Department
    "origin_department", # ForeignKey to Department
    "program",          # ForeignKey to Program
    "lecturer"          # ForeignKey to Lecturer
).all().order_by("course_code", "course_name")</code></pre>

<h3>Department Data</h3>
<p>Provide departments for filtering in template:</p>
<pre><code class="python">departments = Department.objects.all().order_by("name")</code></pre>

<h3>Template Context</h3>
<p>Pass both allocations and departments to template:</p>
<pre><code class="python">{
    "allocations": allocations,  # All course allocations with related objects
    "departments": departments,  # All departments for filtering
}</code></pre>

<h3>Performance Considerations</h3>
<h4>Before select_related</h4>
<ul>
<li>1 query for allocations</li>
<li>+1 query for each allocation's department</li>
<li>+1 query for each allocation's origin_department</li>
<li>+1 query for each allocation's program</li>
<li>+1 query for each allocation's lecturer</li>
<li>Total: 1 + 4N queries (N = number of allocations)</li>
</ul>

<h4>After select_related</h4>
<ul>
<li>1 query for allocations with all related data joined</li>
<li>Total: 1 query regardless of allocation count</li>
</ul>

<h3>Use Cases</h3>
<h4>System-Wide Overview</h4>
<ol>
<li>View all course allocations across all departments</li>
<li>Identify cross-department teaching patterns</li>
<li>Monitor allocation completeness</li>
</ol>

<h4>Department Filtering</h4>
<ol>
<li>Filter allocations by specific department</li>
<li>Compare allocation patterns across departments</li>
<li>Identify resource allocation trends</li>
</ol>

<h4>Data Verification</h4>
<ol>
<li>Verify allocation data completeness</li>
<li>Check for missing lecturer assignments</li>
<li>Validate department assignments</li>
</ol>

<h3>Template Implementation Example</h3>
<pre><code class="django">{% extends "base.html" %}

{% block content %}
&lt;h1&gt;Course Allocations&lt;/h1&gt;

&lt;!-- Department Filter --&gt;
&lt;div class="mb-4"&gt;
    &lt;label for="departmentFilter"&gt;Filter by Department:&lt;/label&gt;
    &lt;select id="departmentFilter" class="form-control"&gt;
        &lt;option value=""&gt;All Departments&lt;/option&gt;
        {% for dept in departments %}
            &lt;option value="{{ dept.id }}"&gt;{{ dept.name }}&lt;/option&gt;
        {% endfor %}
    &lt;/select&gt;
&lt;/div&gt;

&lt;!-- Allocations Table --&gt;
&lt;table class="table table-striped" id="allocationsTable"&gt;
    &lt;thead&gt;
        &lt;tr&gt;
            &lt;th&gt;Course Code&lt;/th&gt;
            &lt;th&gt;Course Name&lt;/th&gt;
            &lt;th&gt;Department&lt;/th&gt;
            &lt;th&gt;Origin Department&lt;/th&gt;
            &lt;th&gt;Program&lt;/th&gt;
            &lt;th&gt;Lecturer&lt;/th&gt;
            &lt;th&gt;Students&lt;/th&gt;
        &lt;/tr&gt;
    &lt;/thead&gt;
    &lt;tbody&gt;
        {% for alloc in allocations %}
        &lt;tr data-department="{{ alloc.department.id }}"&gt;
            &lt;td&gt;{{ alloc.course_code }}&lt;/td&gt;
            &lt;td&gt;{{ alloc.course_name }}&lt;/td&gt;
            &lt;td&gt;{{ alloc.department.name }}&lt;/td&gt;
            &lt;td&gt;{{ alloc.origin_department.name|default:"-" }}&lt;/td&gt;
            &lt;td&gt;{{ alloc.program.name|default:"-" }}&lt;/td&gt;
            &lt;td&gt;{{ alloc.lecturer.display_name|default:"Unassigned" }}&lt;/td&gt;
            &lt;td&gt;{{ alloc.number_of_students|default:0 }}&lt;/td&gt;
        &lt;/tr&gt;
        {% empty %}
        &lt;tr&gt;
            &lt;td colspan="7" class="text-center"&gt;No allocations found&lt;/td&gt;
        &lt;/tr&gt;
        {% endfor %}
    &lt;/tbody&gt;
&lt;/table&gt;

&lt;script&gt;
// JavaScript for department filtering
document.getElementById('departmentFilter').addEventListener('change', function() {
    const selectedDept = this.value;
    const rows = document.querySelectorAll('#allocationsTable tbody tr');
    
    rows.forEach(row => {
        if (!selectedDept || row.dataset.department === selectedDept) {
            row.style.display = '';
        } else {
            row.style.display = 'none';
        }
    });
});
&lt;/script&gt;
{% endblock %}</code></pre>

<h3>Security Considerations</h3>
<ul>
<li><strong>@login_required</strong>: Only authenticated users can access</li>
<li><strong>No department restriction</strong>: Shows all allocations (system-wide view)</li>
<li><strong>Sensitive data</strong>: May contain personnel information</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Use select_related</strong>: Always eager load ForeignKey relationships</li>
<li><strong>Add pagination</strong>: For large datasets, implement pagination</li>
<li><strong>Consider permissions</strong>: Restrict access if needed</li>
<li><strong>Add export functionality</strong>: Allow data export to CSV/Excel</li>
<li><strong>Implement search</strong>: Add search functionality for large lists</li>
</ol>
"""

page3 = DocumentationPage.objects.create(
    title='Course Allocations List View',
    slug='course-allocations-list-view',
    short_description='Display all course allocations with department filtering',
    content=page3_content,
    category=categories['cod-panel-operations'],
    page_type='reference',
    difficulty='beginner',
    order=2,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=10,
    version='1.0'
)
print(f"   Created: {page3.title}")

# Page 4: Models Documentation
page4_content = """
<h2>Course Allocation Models</h2>
<p>Data models for course allocation, archiving, and related entities.</p>

<h3>Core Models Overview</h3>
<h4>CourseAllocation Model</h4>
<p>Active course allocations with current semester data:</p>
<ul>
<li><strong>Course details</strong>: code, name, program</li>
<li><strong>Department associations</strong>: department, origin_department</li>
<li><strong>Lecturer assignment</strong>: lecturer ForeignKey</li>
<li><strong>Student information</strong>: number_of_students</li>
<li><strong>Approval workflow</strong>: DVC approval/rejection status</li>
<li><strong>Timetable status</strong>: submitted_to_tt flag</li>
</ul>

<h4>ArchivedCourseAllocation Model</h4>
<p>Historical archived allocations with additional metadata:</p>
<ul>
<li><strong>All CourseAllocation fields</strong>: Complete copy of allocation data</li>
<li><strong>Archival metadata</strong>: semester, archived_by, archived_at</li>
<li><strong>Historical tracking</strong>: For semester-based archiving</li>
</ul>

<h3>Model Relationships</h3>
<pre><code class="python"># CourseAllocation foreign keys
department = models.ForeignKey(Department, on_delete=models.CASCADE)
origin_department = models.ForeignKey(Department, on_delete=models.SET_NULL, 
                                     null=True, blank=True, related_name='origin_allocations')
program = models.ForeignKey(Program, on_delete=models.SET_NULL, null=True, blank=True)
lecturer = models.ForeignKey(Lecturer, on_delete=models.SET_NULL, null=True, blank=True)

# ArchivedCourseAllocation additional fields
semester = models.CharField(max_length=50)  # e.g., "2024S1", "Fall2024"
archived_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
archived_at = models.DateTimeField(auto_now_add=True)</code></pre>

<h3>Archiving Process Data Flow</h3>
<h4>Active → Archived</h4>
<ol>
<li>User selects semester for archiving</li>
<li>System copies all CourseAllocation records to ArchivedCourseAllocation</li>
<li>Adds semester, archived_by, archived_at metadata</li>
<li>Deletes original CourseAllocation records</li>
</ol>

<h4>Archived → Active (Restore)</h4>
<ol>
<li>User selects semester to restore</li>
<li>System copies ArchivedCourseAllocation records to CourseAllocation</li>
<li>Checks for duplicates (department + course_code + program)</li>
<li>Skips duplicates to prevent conflicts</li>
</ol>

<h3>Data Integrity Considerations</h3>
<h4>Duplicate Prevention</h4>
<pre><code class="python">exists = CourseAllocation.objects.filter(
    department=dept,
    course_code__iexact=a.course_code,
    program=a.program
).exists()</code></pre>

<h4>Transaction Safety</h4>
<pre><code class="python">with transaction.atomic():
    # Archive and delete operations
    # Either all succeed or all fail
    pass</code></pre>

<h3>Query Patterns</h3>
<h4>Get Department Allocations</h4>
<pre><code class="python">allocations = CourseAllocation.objects.filter(department=dept).select_related(
    "lecturer", "program", "origin_department"
).order_by("course_code")</code></pre>

<h4>Get Archived by Semester</h4>
<pre><code class="python">archived_qs = ArchivedCourseAllocation.objects.filter(
    department=dept, 
    semester=semester
).order_by("-archived_at")</code></pre>

<h4>Group Archived by Semester</h4>
<pre><code class="python">from collections import defaultdict
archived_by_semester = defaultdict(list)
for a in archived_qs:
    archived_by_semester[a.semester].append(a)</code></pre>

<h3>Migration Considerations</h3>
<ul>
<li><strong>Backward compatibility</strong>: Ensure old code works with new models</li>
<li><strong>Data migration</strong>: Plan migration of existing archival data</li>
<li><strong>Index optimization</strong>: Add indexes for common queries</li>
<li><strong>Field consistency</strong>: Keep ArchivedCourseAllocation fields synchronized</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Consistent field names</strong>: Keep same field names between models</li>
<li><strong>Comprehensive copying</strong>: Copy all relevant fields during archiving</li>
<li><strong>Audit trails</strong>: Keep archived_by and archived_at for accountability</li>
<li><strong>Semester naming convention</strong>: Establish clear semester naming</li>
<li><strong>Regular cleanup</strong>: Schedule regular archival of old data</li>
</ol>
"""

page4 = DocumentationPage.objects.create(
    title='Course Allocation Models Reference',
    slug='course-allocation-models-reference',
    short_description='Data models for course allocation and archiving',
    content=page4_content,
    category=categories['data-archiving-cleanup'],
    page_type='reference',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=12,
    version='1.0'
)
print(f"   Created: {page4.title}")

# ============================================================
# 4. CREATE SECTIONS WITH CODE EXAMPLES
# ============================================================

print("\n4. CREATING SECTIONS WITH DETAILED CODE EXAMPLES...")

# Section 1: Archive & Delete Implementation
section1 = DocumentationSection.objects.create(
    page=page1,
    title='Archive & Delete Implementation Details',
    content='''
<h3>Complete Archive & Delete Function</h3>
<p>Detailed implementation of the archive and delete operation with error handling.</p>
''',
    order=1,
    is_active=True,
    slug='archive-delete-implementation'
)
print(f"   Created section: {section1.title}")

# Code Example 1: Archive & Delete
code1 = CodeExample.objects.create(
    section=section1,
    title='Archive & Delete Function - Full Code',
    code='''@login_required
def cod_panel_clear(request):
    """
    Page for COD to archive/delete all allocations for their department (by semester),
    view archived semesters, restore or permanently delete archived semester data.
    """
    user = request.user
    dept = detect_user_department(user)
    if not dept:
        messages.error(request, "Unable to detect your department. Please contact admin.")
        return redirect("cod_panel")

    # active allocations for this COD's department
    allocations = CourseAllocation.objects.filter(department=dept).select_related(
        "lecturer", "program", "origin_department"
    ).order_by("course_code")

    # archived allocations for this department (all semesters)
    archived_qs = ArchivedCourseAllocation.objects.filter(department=dept).order_by("-archived_at")

    # group archived allocations by semester for display
    from collections import defaultdict
    archived_by_semester = defaultdict(list)
    for a in archived_qs:
        archived_by_semester[a.semester].append(a)

    if request.method == "POST":
        action = request.POST.get("action")

        # ---------- Archive & Delete ----------
        if action == "archive_delete":
            semester = (request.POST.get("semester") or "").strip()
            if not semester:
                messages.error(request, "Semester is required to archive allocations. Example: 1 or 2025S1")
                return redirect("cod_panel_clear")

            if not allocations.exists():
                messages.warning(request, "No course allocations found to archive for your department.")
                return redirect("cod_panel_clear")

            with transaction.atomic():
                archived_objs = []
                for a in allocations:
                    archived_objs.append(ArchivedCourseAllocation(
                        department=dept,
                        semester=semester,
                        archived_by=user,
                        course_code=a.course_code,
                        course_name=a.course_name,
                        origin_department=a.origin_department,
                        program=a.program,
                        lecturer=a.lecturer,
                        number_of_students=a.number_of_students or 0,
                        approved_by_dvc=a.approved_by_dvc,
                        rejected_by_dvc=a.rejected_by_dvc,
                        reason_for_disapproval=a.reason_for_disapproval,
                        submitted_to_tt=a.submitted_to_tt,
                    ))
                ArchivedCourseAllocation.objects.bulk_create(archived_objs)
                count, _ = allocations.delete()
                messages.success(request, f"Archived and deleted {count} allocations for {dept.name} (semester: {semester}).")
            return redirect("cod_panel_clear")

        # ---------- Restore (publish) archived semester ----------
        if action == "restore_semester":
            semester = (request.POST.get("semester") or "").strip()
            if not semester:
                messages.error(request, "Please specify a semester to restore.")
                return redirect("cod_panel_clear")

            archived = ArchivedCourseAllocation.objects.filter(department=dept, semester=semester)
            if not archived.exists():
                messages.warning(request, f"No archived allocations found for semester '{semester}'.")
                return redirect("cod_panel_clear")

            created = 0
            skipped = 0
            with transaction.atomic():
                for a in archived:
                    # avoid duplicate creation: same department + course_code + program
                    exists = CourseAllocation.objects.filter(
                        department=dept,
                        course_code__iexact=a.course_code,
                        program=a.program
                    ).exists()
                    if exists:
                        skipped += 1
                        continue
                    CourseAllocation.objects.create(
                        course_code=a.course_code,
                        course_name=a.course_name,
                        department=dept,
                        origin_department=a.origin_department,
                        program=a.program,
                        lecturer=a.lecturer,
                        number_of_students=a.number_of_students or 0,
                        approved_by_dvc=a.approved_by_dvc,
                        rejected_by_dvc=a.rejected_by_dvc,
                        reason_for_disapproval=a.reason_for_disapproval,
                        submitted_to_tt=a.submitted_to_tt,
                    )
                    created += 1
            messages.success(request, f"Restore complete for semester '{semester}': created {created}, skipped {skipped}.")
            return redirect("cod_panel_clear")

        # ---------- Permanently delete archived semester ----------
        if action == "delete_archived_semester":
            semester = (request.POST.get("semester") or "").strip()
            if not semester:
                messages.error(request, "Please specify a semester to delete archived data for.")
                return redirect("cod_panel_clear")

            qs = ArchivedCourseAllocation.objects.filter(department=dept, semester=semester)
            cnt = qs.count()
            qs.delete()
            messages.success(request, f"Permanently deleted {cnt} archived allocation(s) for semester '{semester}'.")
            return redirect("cod_panel_clear")

    # GET -> render page
    return render(request, "cod_panel_clear.html", {
        "dept": dept,
        "allocations": allocations,
        "archived_by_semester": dict(archived_by_semester),
    })''',
    language='python',
    order=1,
    description='Complete implementation of archive, restore, and delete operations'
)

# Section 2: Auto Allocation Implementation
section2 = DocumentationSection.objects.create(
    page=page2,
    title='Auto Allocation Implementation Details',
    content='''
<h3>Complete Auto Allocation Function</h3>
<p>Detailed implementation of automatic course allocation with semester management.</p>
''',
    order=1,
    is_active=True,
    slug='auto-allocation-implementation'
)
print(f"   Created section: {section2.title}")

# Code Example 2: Auto Allocation
code2 = CodeExample.objects.create(
    section=section2,
    title='Auto Allocate Courses - Full Code',
    code='''@login_required
def auto_allocate_courses(request):
    user = request.user
    dept = detect_user_department(user)
    if not dept:
        messages.error(request, "Unable to detect your department. Please contact admin.")
        return redirect("cod_panel")

    if request.method == "POST":
        selected_semester = int(request.POST.get("semester"))
        programs = Program.objects.filter(department=dept)

        # === 1️⃣ Delete old course allocations for this department and semester ===
        old_allocations = CourseAllocation.objects.filter(
            department=dept,
            program__in=programs,
            course_code__in=ProgramCourse.objects.filter(
                program__in=programs,
                semester=selected_semester
            ).values_list("course_code", flat=True)
        )
        deleted_count = old_allocations.count()
        old_allocations.delete()

        # === 2️⃣ Fetch valid courses for selected semester ===
        all_courses = ProgramCourse.objects.filter(
            program__in=programs,
            semester=selected_semester
        ).order_by("program__name", "course_code")

        # === 3️⃣ Ensure semester distribution if all are semester 1 ===
        for program in programs:
            prog_courses = list(ProgramCourse.objects.filter(program=program))
            if prog_courses and all(c.semester == 1 for c in prog_courses):
                half = len(prog_courses) // 2
                for c in prog_courses[:half]:
                    c.semester = 1
                    c.save()
                for c in prog_courses[half:]:
                    c.semester = 2
                    c.save()

        # === 4️⃣ Allocate courses ===
        lecturers = list(Lecturer.objects.filter(department=dept))
        other_lecturers = list(Lecturer.objects.exclude(department=dept))

        for course in all_courses:
            allocation = CourseAllocation.objects.create(
                course_code=course.course_code,
                course_name=course.course_name,
                program=course.program,
                department=dept,
                origin_department=dept
            )

            # Auto-assign lecturers fairly
            if lecturers:
                lecturer_allocs = {lec: CourseAllocation.objects.filter(lecturer=lec).count() for lec in lecturers}
                available_lecturers = [lec for lec, cnt in lecturer_allocs.items() if cnt < 6]
                allocation.lecturer = choice(available_lecturers or lecturers)
            elif other_lecturers:
                allocation.lecturer = choice(other_lecturers)

            allocation.save()

        messages.success(
            request,
            f"Semester {selected_semester} courses re-allocated successfully. "
            f"{deleted_count} old allocations removed and {all_courses.count()} courses newly assigned."
        )

    # === 5️⃣ Prepare allocations for display ===
    allocations = []
    for alloc in CourseAllocation.objects.filter(department=dept).select_related("lecturer", "program"):
        course = ProgramCourse.objects.filter(program=alloc.program, course_code=alloc.course_code).first()
        allocations.append({
            "program": alloc.program.name,
            "course_code": alloc.course_code,
            "course_name": alloc.course_name,
            "semester": course.semester if course else "N/A",
            "lecturer": alloc.lecturer.display_name if alloc.lecturer else "Unassigned"
        })

    return render(request, "auto_allocate_courses.html", {
        "department": dept,
        "allocations": allocations
    })''',
    language='python',
    order=1,
    description='Complete implementation of automatic course allocation'
)

# Section 3: Collections defaultdict Usage
section3 = DocumentationSection.objects.create(
    page=page1,
    title='Using defaultdict for Data Grouping',
    content='''
<h3>Collections defaultdict for Efficient Grouping</h3>
<p>How to use Python's <code>defaultdict</code> from the collections module for grouping data.</p>
''',
    order=2,
    is_active=True,
    slug='defaultdict-usage'
)
print(f"   Created section: {section3.title}")

# Code Example 3: defaultdict Usage
code3 = CodeExample.objects.create(
    section=section3,
    title='defaultdict for Semester Grouping',
    code='''from collections import defaultdict

# Example 1: Basic defaultdict usage
def group_by_semester(allocations):
    """
    Group allocations by semester using defaultdict.
    
    Args:
        allocations: QuerySet of ArchivedCourseAllocation objects
        
    Returns:
        dict: {semester: [allocation1, allocation2, ...]}
    """
    grouped = defaultdict(list)
    for alloc in allocations:
        grouped[alloc.semester].append(alloc)
    return dict(grouped)  # Convert to regular dict for template

# Example 2: Multiple grouping criteria
def group_by_semester_and_program(allocations):
    """
    Group allocations by semester and then by program.
    
    Returns:
        dict: {semester: {program: [allocations]}}
    """
    grouped = defaultdict(lambda: defaultdict(list))
    
    for alloc in allocations:
        grouped[alloc.semester][alloc.program].append(alloc)
    
    # Convert nested defaultdicts to regular dicts
    result = {}
    for semester, programs in grouped.items():
        result[semester] = dict(programs)
    
    return result

# Example 3: Counting with defaultdict
def count_by_semester(allocations):
    """
    Count allocations per semester.
    
    Returns:
        dict: {semester: count}
    """
    counts = defaultdict(int)
    
    for alloc in allocations:
        counts[alloc.semester] += 1
    
    return dict(counts)

# Example 4: Template context preparation
def prepare_archive_context(department):
    """
    Prepare context data for archive template.
    """
    # Get archived allocations
    archived_qs = ArchivedCourseAllocation.objects.filter(
        department=department
    ).order_by("-archived_at")
    
    # Group by semester using defaultdict
    from collections import defaultdict
    archived_by_semester = defaultdict(list)
    
    for a in archived_qs:
        archived_by_semester[a.semester].append(a)
    
    # Convert to regular dict for template
    # Templates can't handle defaultdict directly
    archived_by_semester_dict = dict(archived_by_semester)
    
    # Sort semesters by most recent
    sorted_semesters = sorted(
        archived_by_semester_dict.keys(),
        reverse=True,
        key=lambda x: (
            # Sort by year then semester
            int(x[:4]) if x[:4].isdigit() else 0,
            x
        )
    )
    
    return {
        'archived_by_semester': archived_by_semester_dict,
        'sorted_semesters': sorted_semesters,
        'total_archived': archived_qs.count(),
    }

# Example 5: Complex grouping with statistics
def get_semester_statistics(department):
    """
    Get detailed statistics for each archived semester.
    """
    archived_qs = ArchivedCourseAllocation.objects.filter(department=department)
    
    stats = defaultdict(lambda: {
        'count': 0,
        'lecturers': set(),
        'programs': set(),
        'total_students': 0,
        'archived_dates': set(),
    })
    
    for alloc in archived_qs:
        semester = alloc.semester
        stats[semester]['count'] += 1
        
        if alloc.lecturer:
            stats[semester]['lecturers'].add(alloc.lecturer.name)
        
        if alloc.program:
            stats[semester]['programs'].add(alloc.program.name)
        
        stats[semester]['total_students'] += alloc.number_of_students or 0
        
        if alloc.archived_at:
            stats[semester]['archived_dates'].add(
                alloc.archived_at.strftime('%Y-%m-%d')
            )
    
    # Convert sets to lists for JSON serialization
    result = {}
    for semester, data in stats.items():
        result[semester] = {
            'count': data['count'],
            'lecturer_count': len(data['lecturers']),
            'program_count': len(data['programs']),
            'total_students': data['total_students'],
            'archived_dates': sorted(data['archived_dates']),
        }
    
    return result''',
    language='python',
    order=1,
    description='Examples of using defaultdict for data grouping and statistics'
)

# Section 4: Template Examples
section4 = DocumentationSection.objects.create(
    page=page1,
    title='Template Implementation',
    content='''
<h3>HTML Templates for Archive Interface</h3>
<p>Example templates for the archive, restore, and delete interface.</p>
''',
    order=3,
    is_active=True,
    slug='template-implementation'
)
print(f"   Created section: {section4.title}")

# Code Example 4: Template Code
code4 = CodeExample.objects.create(
    section=section4,
    title='cod_panel_clear.html Template',
    code='''<!-- cod_panel_clear.html -->
{% extends "base.html" %}

{% block content %}
<div class="container">
    <h1>Course Allocation Archive & Clear</h1>
    <p class="lead">Department: {{ dept.name }}</p>
    
    <!-- Active Allocations Section -->
    <div class="card mb-4">
        <div class="card-header bg-primary text-white">
            <h2 class="h5 mb-0">Active Allocations ({{ allocations|length }})</h2>
        </div>
        <div class="card-body">
            {% if allocations %}
            <form method="post" class="mb-4" id="archiveForm">
                {% csrf_token %}
                <input type="hidden" name="action" value="archive_delete">
                
                <div class="row">
                    <div class="col-md-6">
                        <div class="form-group">
                            <label for="semester">Archive as Semester:</label>
                            <input type="text" 
                                   class="form-control" 
                                   id="semester" 
                                   name="semester" 
                                   placeholder="e.g., 2024S1, Fall2024, 1"
                                   required>
                            <small class="form-text text-muted">
                                Enter semester identifier for archived data
                            </small>
                        </div>
                    </div>
                    <div class="col-md-6 align-self-end">
                        <button type="submit" 
                                class="btn btn-warning"
                                onclick="return confirm('Archive and delete ALL current allocations? This cannot be undone.')">
                            📦 Archive & Delete All
                        </button>
                    </div>
                </div>
            </form>
            
            <div class="table-responsive">
                <table class="table table-sm table-hover">
                    <thead>
                        <tr>
                            <th>Course Code</th>
                            <th>Course Name</th>
                            <th>Program</th>
                            <th>Lecturer</th>
                            <th>Students</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for alloc in allocations %}
                        <tr>
                            <td>{{ alloc.course_code }}</td>
                            <td>{{ alloc.course_name }}</td>
                            <td>{{ alloc.program.name|default:"-" }}</td>
                            <td>{{ alloc.lecturer.display_name|default:"Unassigned" }}</td>
                            <td>{{ alloc.number_of_students|default:0 }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% else %}
            <div class="alert alert-info">
                No active allocations found for your department.
            </div>
            {% endif %}
        </div>
    </div>
    
    <!-- Archived Semesters Section -->
    <div class="card">
        <div class="card-header bg-secondary text-white">
            <h2 class="h5 mb-0">Archived Semesters</h2>
        </div>
        <div class="card-body">
            {% if archived_by_semester %}
                {% for semester, allocations in archived_by_semester.items %}
                <div class="card mb-3">
                    <div class="card-header">
                        <h3 class="h6 mb-0">
                            Semester: <strong>{{ semester }}</strong>
                            <span class="badge badge-info">{{ allocations|length }} allocations</span>
                        </h3>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-8">
                                <p class="mb-2">
                                    <small>
                                        Archived: {{ allocations.0.archived_at|date:"Y-m-d H:i" }}
                                        by {{ allocations.0.archived_by.username|default:"System" }}
                                    </small>
                                </p>
                            </div>
                            <div class="col-md-4 text-right">
                                <!-- Restore Form -->
                                <form method="post" class="d-inline">
                                    {% csrf_token %}
                                    <input type="hidden" name="action" value="restore_semester">
                                    <input type="hidden" name="semester" value="{{ semester }}">
                                    <button type="submit" 
                                            class="btn btn-sm btn-success"
                                            onclick="return confirm('Restore semester {{ semester }}?')">
                                        ↻ Restore
                                    </button>
                                </form>
                                
                                <!-- Delete Form -->
                                <form method="post" class="d-inline">
                                    {% csrf_token %}
                                    <input type="hidden" name="action" value="delete_archived_semester">
                                    <input type="hidden" name="semester" value="{{ semester }}">
                                    <button type="submit" 
                                            class="btn btn-sm btn-danger"
                                            onclick="return confirm('Permanently delete semester {{ semester }}? This cannot be undone.')">
                                        🗑️ Delete
                                    </button>
                                </form>
                            </div>
                        </div>
                        
                        <!-- Archived Allocations Preview -->
                        <div class="mt-3">
                            <button class="btn btn-sm btn-outline-secondary" 
                                    type="button" 
                                    data-toggle="collapse" 
                                    data-target="#collapse{{ forloop.counter }}">
                                Show/Hide {{ allocations|length }} allocations
                            </button>
                            
                            <div class="collapse mt-2" id="collapse{{ forloop.counter }}">
                                <div class="table-responsive">
                                    <table class="table table-sm">
                                        <thead>
                                            <tr>
                                                <th>Course</th>
                                                <th>Program</th>
                                                <th>Lecturer</th>
                                                <th>Students</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for alloc in allocations|slice:":5" %}
                                            <tr>
                                                <td>{{ alloc.course_code }} - {{ alloc.course_name }}</td>
                                                <td>{{ alloc.program.name|default:"-" }}</td>
                                                <td>{{ alloc.lecturer.display_name|default:"Unassigned" }}</td>
                                                <td>{{ alloc.number_of_students }}</td>
                                            </tr>
                                            {% endfor %}
                                            {% if allocations|length > 5 %}
                                            <tr>
                                                <td colspan="4" class="text-center">
                                                    <em>... and {{ allocations|length|add:"-5" }} more</em>
                                                </td>
                                            </tr>
                                            {% endif %}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                {% endfor %}
            {% else %}
            <div class="alert alert-warning">
                No archived semesters found.
            </div>
            {% endif %}
        </div>
    </div>
</div>

<!-- JavaScript for enhanced UX -->
<script>
document.addEventListener('DOMContentLoaded', function() {
    // Archive form validation
    const archiveForm = document.getElementById('archiveForm');
    if (archiveForm) {
        archiveForm.addEventListener('submit', function(e) {
            const semesterInput = document.getElementById('semester');
            if (!semesterInput.value.trim()) {
                e.preventDefault();
                alert('Please enter a semester identifier.');
                semesterInput.focus();
                return false;
            }
        });
    }
    
    // Confirmation for delete buttons
    const deleteButtons = document.querySelectorAll('button[onclick*="delete"]');
    deleteButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            if (!confirm('This action cannot be undone. Continue?')) {
                e.preventDefault();
                return false;
            }
        });
    });
});
</script>
{% endblock %}''',
    language='django',
    order=1,
    description='HTML template for the archive and clear interface'
)

# ============================================================
# 5. ASSIGN TAGS TO PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO PAGES...")

# Define tag assignments
tag_assignments = {
    page1: ['cod-panel', 'data-archiving', 'data-cleanup', 'semester-management'],
    page2: ['auto-allocation', 'cod-panel', 'semester-management'],
    page3: ['course-allocation', 'cod-panel', 'department-operations'],
    page4: ['course-allocation', 'data-archiving'],
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
    (page1, page4, "View data models for archiving"),
    (page1, page2, "Auto allocate courses after clearing"),
    (page2, page3, "View all allocations after auto allocation"),
    (page3, page1, "Archive allocations when complete"),
    (page4, page1, "See archive implementation using these models"),
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
print("COD PANEL DOCUMENTATION COMPLETE")
print("=" * 80)

# Count statistics
total_pages = DocumentationPage.objects.count()
total_sections = DocumentationSection.objects.count()
total_code_examples = CodeExample.objects.count()
total_links = InternalLink.objects.count()

print(f"""
📊 DOCUMENTATION STATISTICS:
   Total Pages Created:    {4}
   Total Sections:         {total_sections}
   Total Code Examples:    {total_code_examples}
   Total Internal Links:   {total_links}

📚 NEW COD PANEL CONTENT:
   1. COD Panel Archive & Clear Operations
   2. Auto Allocate Courses Function
   3. Course Allocations List View
   4. Course Allocation Models Reference

🔧 KEY FEATURES DOCUMENTED:
   • Semester-based data archiving and deletion
   • Archived data restoration with duplicate prevention
   • Permanent deletion of archived semesters
   • Automatic course allocation with fair distribution
   • Semester distribution logic
   • Efficient data loading with select_related
   • Data model relationships and architecture
   • defaultdict usage for data grouping

💻 TECHNICAL IMPLEMENTATION:
   • Complete Django view implementations
   • Transaction-safe operations with atomic()
   • Efficient database queries with select_related
   • Defaultdict patterns for data grouping
   • Template examples with JavaScript enhancements

🔗 INTEGRATION POINTS:
   • Links between archive operations and models
   • Connection between auto allocation and viewing
   • Model references for data architecture

🚀 ACCESS POINTS:
   Archive Operations: /documentation/page/cod-panel-archive-clear-operations/
   Auto Allocation:    /documentation/page/auto-allocate-courses-function/
   Allocations List:   /documentation/page/course-allocations-list-view/
   Models Reference:   /documentation/page/course-allocation-models-reference/

🎯 OPERATIONAL WORKFLOWS:
   1. Archive old semester data before new allocations
   2. Use auto allocation for initial assignments
   3. Review allocations in list view
   4. Make manual adjustments as needed
   5. Submit for approval when complete

✅ COD Panel documentation successfully created!
""")

print("=" * 80)
print("Documentation includes:")
print("• Complete function implementations")
print("• Database model relationships")
print("• Template examples with JavaScript")
print("• Defaultdict usage patterns")
print("• Error handling and validation")
print("• Performance optimization techniques")
print("=" * 80)