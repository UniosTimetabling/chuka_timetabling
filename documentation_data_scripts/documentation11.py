#!/usr/bin/env python
"""
COMPREHENSIVE TIMETABLING SYSTEM DOCUMENTATION
This script creates comprehensive documentation for the entire timetabling system
including Student Portal, Class Rep System, Sudo Management, Timetable Views, and more.
Run: python manage.py shell < this_script.py
"""

import os
import django
import sys

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
django.setup()

from documentation.models import (
    DocumentationCategory, DocumentationPage, DocumentationSection, 
    CodeExample, DocumentationTag, PageTag, InternalLink
)
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import transaction

print("=" * 80)
print("TIMETABLING SYSTEM COMPREHENSIVE DOCUMENTATION")
print("=" * 80)

# Get admin user for documentation
try:
    author = User.objects.filter(is_superuser=True).first()
    if not author:
        print("ERROR: No admin user found. Please create an admin user first.")
        sys.exit(1)
    print(f"Using admin: {author.username}")
except Exception as e:
    print(f"Error with user: {e}")
    sys.exit(1)

# ============================================================
# 1. CHECK AND CREATE CATEGORIES
# ============================================================

print("\n1. CHECKING AND CREATING CATEGORIES...")

categories_data = [
    {
        'name': 'Student Portal System',
        'slug': 'student-portal-system',
        'description': 'Student-facing portal for timetable viewing and access',
        'icon': 'fas fa-graduation-cap',
        'order': 1,
        'access_level': 'all',
    },
    {
        'name': 'Class Representative System',
        'slug': 'class-representative-system',
        'description': 'Class rep management, login, and minimal timetable',
        'icon': 'fas fa-user-tie',
        'order': 2,
        'access_level': 'department',
    },
    {
        'name': 'Superuser Administration',
        'slug': 'superuser-administration',
        'description': 'Superuser management interfaces for system configuration',
        'icon': 'fas fa-user-shield',
        'order': 3,
        'access_level': 'admin',
    },
    {
        'name': 'Timetable Management',
        'slug': 'timetable-management',
        'description': 'Main timetable creation, editing, and viewing systems',
        'icon': 'fas fa-calendar-alt',
        'order': 4,
        'access_level': 'management',
    },
    {
        'name': 'Program and Course Management',
        'slug': 'program-course-management',
        'description': 'Program, department, faculty, and course allocation management',
        'icon': 'fas fa-university',
        'order': 5,
        'access_level': 'management',
    },
    {
        'name': 'Exam Timetable System',
        'slug': 'exam-timetable-system',
        'description': 'Exam scheduling and management systems',
        'icon': 'fas fa-file-alt',
        'order': 6,
        'access_level': 'department',
    },
    {
        'name': 'Merged Course Groups',
        'slug': 'merged-course-groups',
        'description': 'Auto and manual course merging for scheduling efficiency',
        'icon': 'fas fa-object-group',
        'order': 7,
        'access_level': 'management',
    },
    {
        'name': 'User and Role Management',
        'slug': 'user-role-management',
        'description': 'User accounts, groups, and role-based access control',
        'icon': 'fas fa-users-cog',
        'order': 8,
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
    {'name': 'Student Portal', 'slug': 'student-portal', 'color': '#3498db'},
    {'name': 'Class Rep', 'slug': 'class-rep', 'color': '#2ecc71'},
    {'name': 'Superuser', 'slug': 'superuser', 'color': '#e74c3c'},
    {'name': 'Timetable', 'slug': 'timetable', 'color': '#9b59b6'},
    {'name': 'Exam', 'slug': 'exam', 'color': '#f39c12'},
    {'name': 'Program Management', 'slug': 'program-management', 'color': '#1abc9c'},
    {'name': 'Course Allocation', 'slug': 'course-allocation', 'color': '#34495e'},
    {'name': 'Merged Courses', 'slug': 'merged-courses', 'color': '#d35400'},
    {'name': 'User Management', 'slug': 'user-management', 'color': '#27ae60'},
    {'name': 'Role Management', 'slug': 'role-management', 'color': '#8e44ad'},
    {'name': 'DVC', 'slug': 'dvc', 'color': '#c0392b'},
    {'name': 'Director', 'slug': 'director', 'color': '#16a085'},
    {'name': 'Admin', 'slug': 'admin', 'color': '#2980b9'},
    {'name': 'Faculty', 'slug': 'faculty', 'color': '#7f8c8d'},
    {'name': 'Department', 'slug': 'department', 'color': '#2c3e50'},
    {'name': 'Authentication', 'slug': 'authentication', 'color': '#e67e22'},
    {'name': 'JSON API', 'slug': 'json-api', 'color': '#3498db'},
    {'name': 'Database', 'slug': 'database', 'color': '#95a5a6'},
    {'name': 'Transaction', 'slug': 'transaction', 'color': '#f1c40f'},
    {'name': 'Views', 'slug': 'views', 'color': '#e74c3c'},
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

# ============================================================
# 3. CREATE DOCUMENTATION PAGES
# ============================================================

print("\n3. CREATING DOCUMENTATION PAGES...")

# ============================================================
# PAGE 1: Student Portal System
# ============================================================

page1_content = """
<h2>Student Portal System</h2>
<p>Complete student-facing system for timetable viewing with support for main timetable, exam timetable, and minimal timetable viewing.</p>

<h3>Overview</h3>
<p>The Student Portal System provides students with:</p>
<ol>
<li><strong>Main Timetable View</strong>: Regular class schedules</li>
<li><strong>Exam Timetable View</strong>: Examination schedules</li>
<li><strong>Minimal Timetable View</strong>: Simplified timetable from class reps</li>
<li><strong>Program Selection</strong>: Filter by program and academic year</li>
</ol>

<h3>Core Components</h3>
<h4>1. Student Portal Entry Point</h4>
<pre><code class="python">def studentportal(request):
    \"\"\"Renders the main student portal landing page.\"\"\"
    return render(request, 'student_portal.html')</code></pre>

<h4>2. Timetable View Controller</h4>
<pre><code class="python">@transaction.atomic
def timetable_view(request, program_id=None, timetable_type=None):
    \"\"\"
    Handles both page load and AJAX timetable fetch:
    - Student selects Program + Year
    - Timetable returns only courses belonging to that academic year
      based on ProgramCourse.year
    \"\"\"
    # Implementation details...
</code></pre>

<h3>Key Features</h3>
<h4>Academic Year Filtering</h4>
<p>Strict filtering based on ProgramCourse.year:</p>
<pre><code class="python"># STRICT YEAR FILTER based on ProgramCourse (CORE FIX)
program_courses = ProgramCourse.objects.filter(
    program_id=program_id,
    year=year
).values_list("course_code", flat=True)</code></pre>

<h4>Support for Merged Courses</h4>
<p>Handles both auto-merged and manually merged course groups:</p>
<pre><code class="python"># Support for merged courses (auto + manual)
merged_base_ids = set()

auto_merged = AutoMergedExamGroup.objects.filter(
    merged_courses__in=program_alloc_qs
).values_list("base_course_id", flat=True)
merged_base_ids.update(auto_merged)

manual_merged = MergedCourseGroup.objects.filter(
    merged_courses__in=program_alloc_qs
).values_list("base_course_id", flat=True)
merged_base_ids.update(manual_merged)</code></pre>

<h3>Data Models Used</h3>
<h4>Primary Models</h4>
<ul>
<li><code>Program</code>: Academic programs</li>
<li><code>ProgramCourse</code>: Courses per program and year</li>
<li><code>CourseAllocation</code>: Course assignment to programs</li>
<li><code>Timetable</code>: Main class schedule</li>
<li><code>ExamTimetable</code>: Exam schedule</li>
<li><code>MinimalTimetable</code>: Class rep simplified timetable</li>
</ul>

<h4>Merged Course Models</h4>
<ul>
<li><code>AutoMergedExamGroup</code>: Automatically merged exam groups</li>
<li><code>MergedCourseGroup</code>: Manually merged course groups</li>
</ul>

<h3>URL Structure</h3>
<pre><code class="python">urlpatterns = [
    path('studentportal/', views.studentportal, name='studentportal'),
    path('timetable/view/', views.timetable_view, name='timetable_view'),
    path('timetable/view/&lt;int:program_id&gt;/&lt;str:timetable_type&gt;/', 
         views.timetable_view, name='timetable_view_detail'),
]</code></pre>

<h3>JSON Response Structure</h3>
<h4>Main Timetable Response</h4>
<pre><code class="json">{
    "program_name": "Computer Science",
    "year": 2,
    "timetable_type": "main",
    "has_entries": true,
    "data": {
        "Monday": [
            {
                "venue": "Room 101",
                "course_code": "CS201",
                "course_name": "Data Structures",
                "lecturer": "Dr. Smith",
                "start": "09:00",
                "end": "11:00"
            }
        ]
    }
}</code></pre>

<h3>Error Handling</h3>
<pre><code class="python">except Exception as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.error(f"Error in timetable_view: {e}")
    return JsonResponse({"error": str(e), "has_entries": False}, status=500)</code></pre>

<h3>Frontend Integration</h3>
<h4>AJAX Request Example</h4>
<pre><code class="javascript">// Fetch timetable for selected program and year
function fetchTimetable(programId, year, type) {
    fetch(`/timetable/view/${programId}/${type}/?year=${year}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(data.error);
            } else {
                displayTimetable(data);
            }
        })
        .catch(error => {
            showError('Failed to load timetable');
        });
}</code></pre>

<h3>Best Practices</h3>
<ol>
<li><strong>Use atomic transactions</strong>: Ensure data consistency</li>
<li><strong>Proper error logging</strong>: Log errors for debugging</li>
<li><strong>Efficient queries</strong>: Use select_related and values_list</li>
<li><strong>Validate input</strong>: Always validate program_id and year</li>
<li><strong>Cache responses</strong>: Consider caching for performance</li>
</ol>
"""

page1 = DocumentationPage.objects.create(
    title='Student Portal System - Complete Guide',
    slug='student-portal-system-complete-guide',
    short_description='Comprehensive guide to student portal system including timetable viewing and program filtering',
    content=page1_content,
    category=categories['student-portal-system'],
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

# ============================================================
# PAGE 2: Class Representative System
# ============================================================

page2_content = """
<h2>Class Representative System</h2>
<p>Complete system for class representative management including login, dashboard, and minimal timetable management.</p>

<h3>Overview</h3>
<p>The Class Rep System provides:</p>
<ol>
<li><strong>Class Rep Login</strong>: Secure authentication with credential reset</li>
<li><strong>Dashboard</strong>: Central management interface</li>
<li><strong>Minimal Timetable</strong>: Simplified timetable management</li>
<li><strong>Program Code Management</strong>: Add and manage program codes</li>
</ol>

<h3>Core Components</h3>
<h4>1. Class Rep Login System</h4>
<pre><code class="python">def classrep_login(request):
    \"\"\"
    Class Rep login + credential reset page.
    Each Program automatically has a ClassRep account created.
    \"\"\"
    # Implementation details...
</code></pre>

<h4>2. Class Rep Dashboard</h4>
<pre><code class="python">@transaction.atomic
def classrep_dashboard(request):
    \"\"\"
    Main dashboard for class representatives with notifications,
    timetable viewing, and program code management.
    \"\"\"
    # Implementation details...
</code></pre>

<h3>Key Features</h3>
<h4>Automatic Account Creation</h4>
<p>Automatically creates ClassRep accounts for each program:</p>
<pre><code class="python">with transaction.atomic():
    for program in Program.objects.all():
        # Skip if already linked to a ClassRep
        if ClassRep.objects.filter(program=program).exists():
            continue

        # Build short code from program name
        base_code = "".join(word[0].upper() for word in program.name.split()[:3])
        reg_no = generate_unique_reg_no(base_code, max_length=20)</code></pre>

<h4>Unique Registration Number Generation</h4>
<pre><code class="python">def generate_unique_reg_no(base_code: str, max_length: int = 20) -> str:
    \"\"\"Generate a unique reg_no that doesn't already exist in ClassRep.\"\"\"
    for _ in range(20):  # retry up to 20 times just in case
        random_suffix = get_random_string(3).upper()
        reg_no = f"REP-{base_code}{random_suffix}"[:max_length]
        if not ClassRep.objects.filter(reg_no=reg_no).exists():
            return reg_no
    # fallback (extremely rare)
    return f"REP-{get_random_string(6).upper()}"[:max_length]</code></pre>

<h4>Program Code Management</h4>
<pre><code class="python"># Handle AJAX submission for adding program codes
if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
    program_id = request.POST.get("program_id")
    code = (request.POST.get("program_code") or "").strip().upper()

    if not (program_id and code):
        return HttpResponseBadRequest("Missing program or code")

    program = get_object_or_404(Program, id=program_id)
    program_code, created = ProgramCode.objects.get_or_create(program=program, code=code)</code></pre>

<h3>Minimal Timetable Management</h3>
<h4>Publish Main to Minimal</h4>
<pre><code class="python">def publish_minimal_timetable(request):
    \"\"\"Copy main timetable entries to minimal timetable.\"\"\"
    rep_id = request.session.get("classrep_id")
    rep = ClassRep.objects.get(id=rep_id)
    
    # Delete old entries
    MinimalTimetable.objects.filter(class_rep=rep).delete()

    # Create minimal copy
    for t in timetable_entries:
        MinimalTimetable.objects.create(
            class_rep=rep,
            course_code=t.course_allocation.course_code,
            course_name=t.course_allocation.course_name,
            day=t.day,
            start_time=t.start_time,
            end_time=t.end_time,
            venue=t.venue,
        )</code></pre>

<h4>Edit Minimal Entries</h4>
<pre><code class="python">def edit_minimal_entry(request, entry_id):
    \"\"\"Edit individual minimal timetable entries.\"\"\"
    rep_id = request.session.get("classrep_id")
    entry = get_object_or_404(MinimalTimetable, id=entry_id, class_rep_id=rep_id)

    if request.method == "POST":
        entry.day = request.POST.get("day")
        entry.start_time = request.POST.get("start_time")
        entry.end_time = request.POST.get("end_time")
        entry.venue = request.POST.get("venue")
        entry.save()
        return redirect("classrep_dashboard")</code></pre>

<h3>Data Models Used</h3>
<h4>Primary Models</h4>
<ul>
<li><code>ClassRep</code>: Class representative accounts</li>
<li><code>Program</code>: Academic programs</li>
<li><code>ProgramCode</code>: Program identification codes</li>
<li><code>MinimalTimetable</code>: Simplified timetable entries</li>
</ul>

<h3>Session Management</h3>
<pre><code class="python"># Store class rep in session
request.session["classrep_id"] = rep.id
request.session["classrep_username"] = rep.username

# Check session in views
rep_id = request.session.get("classrep_id")
if not rep_id:
    return redirect("classrep_login")</code></pre>

<h3>Security Considerations</h3>
<ol>
<li><strong>Password storage</strong>: Passwords stored in plain text (consider hashing)</li>
<li><strong>Session timeout</strong>: Implement proper session expiration</li>
<li><strong>Input validation</strong>: Validate all user inputs</li>
<li><strong>Access control</strong>: Ensure class reps only access their data</li>
</ol>

<h3>Use Cases</h3>
<h4>New Semester Setup</h4>
<ol>
<li>Superuser runs automatic account creation</li>
<li>Class reps receive login credentials</li>
<li>Class reps add program codes</li>
<li>Class reps publish minimal timetables</li>
</ol>

<h4>Daily Operations</h4>
<ol>
<li>Class rep logs into dashboard</li>
<li>Views notifications and timetable</li>
<li>Updates minimal timetable if needed</li>
<li>Manages program codes for their program</li>
</ol>
"""

page2 = DocumentationPage.objects.create(
    title='Class Representative System - Complete Guide',
    slug='class-representative-system-complete-guide',
    short_description='Complete guide to class representative system including login, dashboard, and timetable management',
    content=page2_content,
    category=categories['class-representative-system'],
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

# ============================================================
# PAGE 3: Superuser Administration
# ============================================================

page3_content = """
<h2>Superuser Administration System</h2>
<p>Comprehensive superuser management interfaces for system configuration and user management.</p>

<h3>Overview</h3>
<p>The Superuser Administration System provides:</p>
<ol>
<li><strong>DVC Management</strong>: Deputy Vice Chancellor user management</li>
<li><strong>Director Management</strong>: Timetable director management</li>
<li><strong>User Account Management</strong>: Comprehensive user administration</li>
<li><strong>System Configuration</strong>: Global system settings</li>
</ol>

<h3>Core Components</h3>
<h4>1. DVC Management System</h4>
<pre><code class="python">@sudo_required
def ajax_manage_dvc(request):
    \"\"\"Single AJAX endpoint for DVC + Admin CRUD operations.\"\"\"
    # Implementation details...
</code></pre>

<h4>2. Director and Admin Management</h4>
<pre><code class="python">@login_required
@transaction.atomic
@sudo_required_json
def ajax_manage_timetable(request):
    \"\"\"Single AJAX endpoint for Director + Admin CRUD.\"\"\"
    # Implementation details...
</code></pre>

<h3>Key Features</h3>
<h4>Superuser Decorator</h4>
<pre><code class="python">def sudo_required(view_func):
    \"\"\"Ensure only superusers can access endpoints.\"\"\"
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper</code></pre>

<h4>Username Normalization</h4>
<pre><code class="python">def normalize_username(value: str) -> str:
    \"\"\"Generate safe username from full name.\"\"\"
    safe = slugify(value).replace("-", "_")
    if not safe or not safe[0].isalpha():
        safe = "u_" + safe
    return f"dvc_{safe}"</code></pre>

<h4>Single DVC Enforcement</h4>
<pre><code class="python"># Check if DVC already exists
existing_dvc = User.objects.filter(groups__name="DVC").first()
if existing_dvc:
    return JsonResponse({
        "status": "error",
        "message": f"DVC already exists: {existing_dvc.username}. Please edit or delete first."
    }, status=400)</code></pre>

<h3>User Creation Workflow</h3>
<h4>Create DVC User</h4>
<pre><code class="python">if action == "create_dvc":
    full_name = (request.POST.get("full_name") or "").strip()
    email = (request.POST.get("email") or "").strip()
    custom_username = (request.POST.get("username") or "").strip()
    password = (request.POST.get("password") or "").strip()

    # Validation checks
    if not full_name or not password:
        return JsonResponse({"status": "error", "message": "Required fields missing"}, status=400)

    # Username generation
    username = normalize_username(full_name) if not custom_username else custom_username.lower()
    
    # Create user
    user = User.objects.create(username=username, email=email, is_active=True)
    user.set_password(password)
    user.save()

    # Add to DVC group
    dvc_group, _ = Group.objects.get_or_create(name="DVC")
    user.groups.add(dvc_group)
    create_or_update_orgrole(user, "DVC")</code></pre>

<h4>Create Admin User</h4>
<pre><code class="python">if action == "create_admin":
    # Default username pattern: dvc_admin, dvc_admin1, dvc_admin2, ...
    base_username = "dvc_admin"
    username = base_username
    i = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}{i}"
        i += 1</code></pre>

<h3>CRUD Operations</h3>
<h4>List Users</h4>
<pre><code class="python">if action == "list":
    qs = User.objects.filter(
        groups__name__in=["DVC", "DVC Admins"]
    ).distinct().order_by("username")
    data = []
    for u in qs:
        data.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "full_name": f"{u.first_name} {u.last_name}".strip(),
            "is_active": u.is_active,
            "groups": [g.name for g in u.groups.all()],
        })
    return JsonResponse({"status": "success", "results": data})</code></pre>

<h4>Edit User</h4>
<pre><code class="python">if action == "edit":
    uid = request.POST.get("user_id")
    user = get_object_or_404(User, pk=uid)
    full_name = (request.POST.get("full_name") or "").strip()
    email = (request.POST.get("email") or "").strip()
    is_active = request.POST.get("is_active")
    
    if full_name:
        parts = full_name.split()
        user.first_name = parts[0] if parts else ""
        user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
    if email:
        user.email = email
    if is_active is not None:
        user.is_active = is_active in ("1", "true", "on")
    user.save()</code></pre>

<h3>Data Models Used</h3>
<h4>Primary Models</h4>
<ul>
<li><code>User</code>: Django auth user model</li>
<li><code>Group</code>: Django auth group model</li>
<li><code>OrgRole</code>: Custom organizational roles</li>
</ul>

<h3>Security Features</h3>
<ol>
<li><strong>Superuser-only access</strong>: Restricted to superusers only</li>
<li><strong>Password hashing</strong>: Uses Django's password hashing</li>
<li><strong>Transaction safety</strong>: Atomic transactions for data consistency</li>
<li><strong>Input validation</strong>: Validates all user inputs</li>
</ol>

<h3>Frontend Integration</h3>
<h4>AJAX Operations</h4>
<p>All operations use AJAX for seamless user experience:</p>
<pre><code class="javascript">// Example AJAX call for creating DVC
function createDVC() {
    const formData = new FormData();
    formData.append('action', 'create_dvc');
    formData.append('full_name', document.getElementById('full_name').value);
    formData.append('email', document.getElementById('email').value);
    formData.append('password', document.getElementById('password').value);
    
    fetch('/ajax/manage_dvc/', {
        method: 'POST',
        body: formData,
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showSuccess(data.message);
            loadUserList();
        } else {
            showError(data.message);
        }
    });
}</code></pre>

<h3>Best Practices</h3>
<ol>
<li><strong>Use atomic transactions</strong>: For all database operations</li>
<li><strong>Validate inputs thoroughly</strong>: Prevent invalid data</li>
<li><strong>Implement proper error handling</strong>: User-friendly error messages</li>
<li><strong>Log all admin actions</strong>: For audit trail</li>
<li><strong>Secure password handling</strong>: Never store plain text passwords</li>
</ol>
"""

page3 = DocumentationPage.objects.create(
    title='Superuser Administration System - Complete Guide',
    slug='superuser-administration-system-complete-guide',
    short_description='Complete guide to superuser administration including DVC management, user administration, and system configuration',
    content=page3_content,
    category=categories['superuser-administration'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=25,
    version='1.0'
)
print(f"   Created: {page3.title}")

# ============================================================
# PAGE 4: Timetable Management System
# ============================================================

page4_content = """
<h2>Timetable Management System</h2>
<p>Comprehensive system for creating, editing, and managing timetables with support for merged courses and constraints.</p>

<h3>Overview</h3>
<p>The Timetable Management System provides:</p>
<ol>
<li><strong>Timetable Creation</strong>: Create and edit timetable entries</li>
<li><strong>Constraint Checking</strong>: Validate scheduling constraints</li>
<li><strong>Merged Course Support</strong>: Handle auto and manual merged courses</li>
<li><strong>Bulk Operations</strong>: Import/export and batch operations</li>
</ol>

<h3>Core Components</h3>
<h4>1. Timetable View System</h4>
<pre><code class="python">@transaction.atomic
def timetable_view(request, program_id=None, timetable_type=None):
    \"\"\"
    Main timetable viewing system supporting multiple timetable types
    and academic year filtering.
    \"\"\"
    # Implementation details...
</code></pre>

<h4>2. Constraint Management</h4>
<pre><code class="python">def check_timetable_constraints(program_id, day, start_time, end_time, venue, lecturer):
    \"\"\"
    Check various timetable constraints:
    - Venue availability
    - Lecturer availability
    - Program schedule conflicts
    - Merged course constraints
    \"\"\"
    # Implementation details...
</code></pre>

<h3>Key Features</h3>
<h4>Academic Year Filtering</h4>
<pre><code class="python"># Strict year-based filtering
program_courses = ProgramCourse.objects.filter(
    program_id=program_id,
    year=year
).values_list("course_code", flat=True)

program_alloc_qs = CourseAllocation.objects.filter(
    program_id=program_id,
    course_code__in=program_courses
)</code></pre>

<h4>Merged Course Handling</h4>
<pre><code class="python"># Support for merged courses
merged_base_ids = set()

# Auto-merged courses
auto_merged = AutoMergedExamGroup.objects.filter(
    merged_courses__in=program_alloc_qs
).values_list("base_course_id", flat=True)
merged_base_ids.update(auto_merged)

# Manual merged courses
manual_merged = MergedCourseGroup.objects.filter(
    merged_courses__in=program_alloc_qs
).values_list("base_course_id", flat=True)
merged_base_ids.update(manual_merged)

# Final filter
base_q = (
    models.Q(course_allocation_id__in=alloc_ids)
    | models.Q(course_allocation_id__in=merged_base_ids)
)</code></pre>

<h4>Multiple Timetable Types</h4>
<pre><code class="python"># Minimal Timetable (Class Rep)
if timetable_type == "minimal":
    rep_entries = MinimalTimetable.objects.filter(
        class_rep__program_id=program_id,
        class_rep__year_of_study=year
    ).order_by("day", "start_time")

# Main Timetable
if timetable_type == "main":
    entries = Timetable.objects.filter(base_q).select_related(
        "course_allocation", "course_allocation__lecturer"
    ).order_by("day", "start_time")

# Exam Timetable
if timetable_type == "exam":
    entries = ExamTimetable.objects.filter(base_q).select_related(
        "course_allocation", "course_allocation__lecturer"
    ).order_by("date", "start_time")</code></pre>

<h3>Data Models Used</h3>
<h4>Primary Timetable Models</h4>
<ul>
<li><code>Timetable</code>: Main class timetable entries</li>
<li><code>ExamTimetable</code>: Exam schedule entries</li>
<li><code>MinimalTimetable</code>: Simplified class rep timetable</li>
<li><code>CourseAllocation</code>: Course to program allocation</li>
</ul>

<h4>Supporting Models</h4>
<ul>
<li><code>ProgramCourse</code>: Course to program and year mapping</li>
<li><code>AutoMergedExamGroup</code>: Auto-merged exam groups</li>
<li><code>MergedCourseGroup</code>: Manually merged course groups</li>
<li><code>Program</code>: Academic programs</li>
<li><code>Department</code>: Academic departments</li>
<li><code>Faculty</code>: Academic faculties</li>
</ul>

<h3>Constraint Types</h3>
<h4>Hard Constraints</h4>
<ol>
<li><strong>Venue Availability</strong>: No double-booking of venues</li>
<li><strong>Lecturer Availability</strong>: No lecturer double-booking</li>
<li><strong>Program Conflicts</strong>: No overlapping classes for same program</li>
<li><strong>Time Slot Limits</strong>: Within configured time bounds</li>
</ol>

<h4>Soft Constraints</h4>
<ol>
<li><strong>Preferred Times</strong>: Morning vs afternoon preferences</li>
<li><strong>Room Capacity</strong>: Student count vs room capacity</li>
<li><strong>Accessibility</strong>: Special needs accommodations</li>
<li><strong>Equipment Requirements</strong>: Specialized room equipment</li>
</ol>

<h3>API Endpoints</h3>
<h4>JSON Response Structure</h4>
<pre><code class="json">{
    "program_name": "Computer Science",
    "year": 2,
    "timetable_type": "main",
    "has_entries": true,
    "data": {
        "Monday": [
            {
                "venue": "LT1",
                "course_code": "CS201",
                "course_name": "Data Structures",
                "lecturer": "Dr. Smith",
                "start": "09:00",
                "end": "11:00"
            }
        ]
    }
}</code></pre>

<h4>Error Response</h4>
<pre><code class="json">{
    "error": "Invalid timetable type",
    "has_entries": false
}</code></pre>

<h3>Performance Optimization</h3>
<h4>Efficient Querying</h4>
<pre><code class="python"># Use select_related for foreign keys
entries = Timetable.objects.filter(base_q).select_related(
    "course_allocation", "course_allocation__lecturer"
).order_by("day", "start_time")

# Use values_list for specific fields
alloc_ids = list(program_alloc_qs.values_list("id", flat=True))</code></pre>

<h4>Database Indexes</h4>
<pre><code class="python">class Timetable(models.Model):
    # Fields...
    
    class Meta:
        indexes = [
            models.Index(fields=['day', 'start_time']),
            models.Index(fields=['course_allocation', 'day']),
            models.Index(fields=['venue', 'day', 'start_time']),
        ]</code></pre>

<h3>Use Cases</h3>
<h4>Semester Timetable Creation</h4>
<ol>
<li>Import course allocations</li>
<li>Run auto-scheduler for initial schedule</li>
<li>Manually adjust for constraints</li>
<li>Validate all constraints</li>
<li>Publish timetable</li>
</ol>

<h4>Daily Operations</h4>
<ol>
<li>View timetable for specific program/year</li>
<li>Check for scheduling conflicts</li>
<li>Make ad-hoc adjustments</li>
<li>Notify affected parties</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Use transactions</strong>: For data consistency</li>
<li><strong>Implement caching</strong>: For frequently accessed timetables</li>
<li><strong>Validate constraints</strong>: Before saving changes</li>
<li><strong>Log changes</strong>: For audit trail</li>
<li><strong>Provide rollback</strong>: For mistake recovery</li>
</ol>
"""

page4 = DocumentationPage.objects.create(
    title='Timetable Management System - Complete Guide',
    slug='timetable-management-system-complete-guide',
    short_description='Complete guide to timetable management including creation, editing, constraint checking, and merged course support',
    content=page4_content,
    category=categories['timetable-management'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=20,
    version='1.0'
)
print(f"   Created: {page4.title}")

# ============================================================
# PAGE 5: Program and Course Management
# ============================================================

page5_content = """
<h2>Program and Course Management System</h2>
<p>Complete system for managing academic programs, departments, faculties, and course allocations.</p>

<h3>Overview</h3>
<p>The Program and Course Management System provides:</p>
<ol>
<li><strong>Program Management</strong>: Create and manage academic programs</li>
<li><strong>Department Management</strong>: Department creation and configuration</li>
<li><strong>Faculty Management</strong>: Faculty administration</li>
<li><strong>Course Allocation</strong>: Assign courses to programs and lecturers</li>
</ol>

<h3>Core Components</h3>
<h4>1. Superuser Dashboard</h4>
<pre><code class="python">@sudo_required
def sudo_dashboard(request):
    \"\"\"Main superuser dashboard for program and course management.\"\"\"
    context = {
        "faculties": Faculty.objects.all(),
        "departments": Department.objects.all(),
        "allocations": CourseAllocation.objects.all(),
        "timetables": Timetable.objects.all(),
        "faculty_form": FacultyForm(),
        "department_form": DepartmentForm(),
        "allocation_form": CourseAllocationForm(),
        "timetable_form": TimetableForm(),
        "users": User.objects.all(),
    }
    return render(request, "sudo_dashboard.html", context)</code></pre>

<h4>2. AJAX CRUD Operations</h4>
<pre><code class="python">@sudo_required
@transaction.atomic
def ajax_faculty(request):
    \"\"\"AJAX endpoint for faculty CRUD operations.\"\"\"
    if request.method == "POST":
        action = request.POST.get("action")
        
        if action == "add":
            form = FacultyForm(request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                leader = handle_user_option(
                    request, "leader", obj.name, role="dean", with_admin=True
                )
                obj.leader = leader
                obj.save()
                return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
            return JsonResponse({"success": False, "errors": form.errors})</code></pre>

<h3>Key Features</h3>
<h4>User Option Handling</h4>
<pre><code class="python">def handle_user_option(request, key_existing, entity_name, role=None, with_admin=False):
    \"\"\"
    Decide whether to use an existing user or create a new role-based user.
    \"\"\"
    user = None
    option = request.POST.get(f"{key_existing}_option")

    if option == "existing":
        uid = request.POST.get(key_existing)
        user = User.objects.filter(pk=uid).first()

    elif option == "new" and role:
        user = create_role_user(role, entity_name, with_admin=with_admin)

    return user</code></pre>

<h4>Role-based User Creation</h4>
<pre><code class="python">def create_role_user(role, entity_name, with_admin=False):
    \"\"\"
    Creates a user for a specific role tied to a faculty/department.
    Example: role='dean', entity_name='FSET' -> username='dean_fset'
    \"\"\"
    base_name = normalize_name(entity_name)
    role_username = f"{role}_{base_name}"

    # Create main user
    password = f"{role_username}@2025"
    user, created = User.objects.get_or_create(username=role_username)
    if created:
        user.set_password(password)
        user.save()
        OrgRole.objects.create(title=role_username, user=user)

    # Ensure user is in correct group
    group, _ = Group.objects.get_or_create(name=role)
    if group not in user.groups.all():
        user.groups.add(group)
        user.save()

    # Optional admin user
    if with_admin:
        admin_username = f"{role_username}_admin"
        admin_password = f"{admin_username}@2025"
        admin_user, created = User.objects.get_or_create(username=admin_username)
        if created:
            admin_user.set_password(admin_password)
            admin_user.save()
            OrgRole.objects.create(title=admin_username, user=admin_user)

        admin_group, _ = Group.objects.get_or_create(name=f"{role}_admins")
        if admin_group not in admin_user.groups.all():
            admin_user.groups.add(admin_group)
            admin_user.save()

    return user</code></pre>

<h3>Data Models Used</h3>
<h4>Academic Structure Models</h4>
<ul>
<li><code>Faculty</code>: Academic faculties with leader</li>
<li><code>Department</code>: Academic departments within faculties</li>
<li><code>Program</code>: Academic programs within departments</li>
<li><code>ProgramCourse</code>: Courses offered by programs</li>
</ul>

<h4>Course Management Models</h4>
<ul>
<li><code>CourseAllocation</code>: Course assignment to programs and lecturers</li>
<li><code>ProgramCode</code>: Program identification codes</li>
<li><code>AutoMergedExamGroup</code>: Auto-merged exam groups</li>
<li><code>MergedCourseGroup</code>: Manually merged course groups</li>
</ul>

<h3>Form Management</h3>
<h4>Faculty Form</h4>
<pre><code class="python">class FacultyForm(forms.ModelForm):
    class Meta:
        model = Faculty
        fields = ['name', 'code', 'description']
        
    def clean_name(self):
        name = self.cleaned_data['name']
        if Faculty.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("A faculty with this name already exists.")
        return name</code></pre>

<h4>Department Form</h4>
<pre><code class="python">class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name', 'code', 'faculty', 'description']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['faculty'].queryset = Faculty.objects.all().order_by('name')</code></pre>

<h3>CRUD Operations</h3>
<h4>Add Faculty</h4>
<pre><code class="python">if action == "add":
    form = FacultyForm(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        leader = handle_user_option(
            request, "leader", obj.name, role="dean", with_admin=True
        )
        obj.leader = leader
        obj.save()
        return JsonResponse({"success": True, "id": obj.id, "name": obj.name})</code></pre>

<h4>Edit Department</h4>
<pre><code class="python">elif action == "edit":
    obj = get_object_or_404(Department, pk=request.POST.get("id"))
    form = DepartmentForm(request.POST, instance=obj)
    if form.is_valid():
        obj = form.save()
        return JsonResponse({"success": True, "id": obj.id, "name": obj.name})</code></pre>

<h4>Delete Allocation</h4>
<pre><code class="python">elif action == "delete":
    obj = get_object_or_404(CourseAllocation, pk=request.POST.get("id"))
    obj.delete()
    return JsonResponse({"success": True})</code></pre>

<h3>Use Cases</h3>
<h4>New Academic Year Setup</h4>
<ol>
<li>Create/update faculties and departments</li>
<li>Set up program structures</li>
<li>Define course offerings per program</li>
<li>Allocate courses to lecturers</li>
<li>Configure program codes</li>
</ol>

<h4>Course Management</h4>
<ol>
<li>Add new courses to programs</li>
<li>Assign lecturers to courses</li>
<li>Set up merged course groups</li>
<li>Configure exam groups</li>
<li>Validate allocations</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Use transactions</strong>: For data consistency in CRUD operations</li>
<li><strong>Validate relationships</strong>: Ensure proper faculty-department-program hierarchy</li>
<li><strong>Implement soft delete</strong>: Consider is_active flags instead of hard delete</li>
<li><strong>Maintain audit trail</strong>: Log all changes to academic structure</li>
<li><strong>Validate user assignments</strong>: Ensure users have appropriate roles</li>
</ol>
"""

page5 = DocumentationPage.objects.create(
    title='Program and Course Management System - Complete Guide',
    slug='program-course-management-system-complete-guide',
    short_description='Complete guide to managing academic programs, departments, faculties, and course allocations',
    content=page5_content,
    category=categories['program-course-management'],
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
print(f"   Created: {page5.title}")

# ============================================================
# PAGE 6: User and Role Management System
# ============================================================

page6_content = """
<h2>User and Role Management System</h2>
<p>Comprehensive system for managing users, groups, and role-based access control throughout the timetabling system.</p>

<h3>Overview</h3>
<p>The User and Role Management System provides:</p>
<ol>
<li><strong>User Account Management</strong>: Create, edit, and delete user accounts</li>
<li><strong>Group Management</strong>: Create and manage user groups</li>
<li><strong>Role Assignment</strong>: Assign users to organizational roles</li>
<li><strong>Access Control</strong>: Role-based permissions throughout the system</li>
</ol>

<h3>Core Components</h3>
<h4>1. User Management Interface</h4>
<pre><code class="python">@sudo_required
def sudo_manage_accounts(request):
    \"\"\"Main user and role management interface.\"\"\"
    context = {
        "users": User.objects.all(),
        "leaders": OrgRole.objects.select_related("user").all(),
        "groups": Group.objects.all(),
        "user_form": UserForm(),
        "group_form": GroupForm(),
    }
    return render(request, "sudo_manage_accounts.html", context)</code></pre>

<h4>2. AJAX User Operations</h4>
<pre><code class="python">@sudo_required
def ajax_users(request):
    \"\"\"AJAX endpoint for user CRUD operations.\"\"\"
    if request.method == "POST":
        if request.POST.get("form_action") in ["add_user", "edit_user"]:
            uid = request.POST.get("user_id")
            inst = get_object_or_404(User, pk=uid) if uid else None
            form = UserForm(request.POST, instance=inst)
            if form.is_valid():
                user = form.save(commit=False)
                pwd = form.cleaned_data.get("password")
                if pwd:
                    user.set_password(pwd)
                user.save()
        elif "delete_user" in request.POST:
            get_object_or_404(User, pk=request.POST.get("user_id")).delete()</code></pre>

<h3>Key Features</h3>
<h4>Organizational Roles</h4>
<pre><code class="python">class OrgRole(models.Model):
    \"\"\"Custom organizational roles for users.\"\"\"
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='org_roles')
    title = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'title']
    
    def __str__(self):
        return f"{self.user.username} - {self.title}"</code></pre>

<h4>Role-Based User Creation</h4>
<pre><code class="python">def create_role_user(role, entity_name, with_admin=False):
    \"\"\"
    Creates a user for a specific role tied to a faculty/department.
    Example: role='dean', entity_name='FSET' -> username='dean_fset'
    \"\"\"
    base_name = normalize_name(entity_name)
    role_username = f"{role}_{base_name}"
    password = f"{role_username}@2025"
    
    user, created = User.objects.get_or_create(username=role_username)
    if created:
        user.set_password(password)
        user.save()
        OrgRole.objects.create(title=role_username, user=user)
    
    # Add to appropriate group
    group, _ = Group.objects.get_or_create(name=role)
    user.groups.add(group)</code></pre>

<h3>User Types and Roles</h3>
<h4>System Roles</h4>
<table class="table">
<thead>
<tr><th>Role</th><th>Description</th><th>Groups</th><th>Access Level</th></tr>
</thead>
<tbody>
<tr><td>Superuser</td><td>Full system access</td><td>-</td><td>Admin</td></tr>
<tr><td>DVC</td><td>Deputy Vice Chancellor</td><td>DVC</td><td>Management</td></tr>
<tr><td>DVC Admin</td><td>DVC Assistant</td><td>DVC Admins</td><td>Management</td></tr>
<tr><td>Director</td><td>Timetable Director</td><td>Director Timetable</td><td>Management</td></tr>
<tr><td>Timetable Admin</td><td>Timetable Administrator</td><td>Timetable Admins</td><td>Department</td></tr>
<tr><td>Dean</td><td>Faculty Dean</td><td>dean</td><td>Department</td></tr>
<tr><td>COD</td><td>Chair of Department</td><td>cod</td><td>Department</td></tr>
<tr><td>Lecturer</td><td>Course Lecturer</td><td>lecturer</td><td>Department</td></tr>
<tr><td>Class Rep</td><td>Class Representative</td><td>-</td><td>Program</td></tr>
<tr><td>Student</td><td>Student User</td><td>-</td><td>Student</td></tr>
</tbody>
</table>

<h3>Access Control Implementation</h3>
<h4>Decorator-Based Access Control</h4>
<pre><code class="python">def sudo_required(view_func):
    \"\"\"Ensure only superusers can access endpoints.\"\"\"
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper

def sudo_required_json(view_func):
    \"\"\"Ensure only superusers can access JSON endpoints.\"\"\"
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper</code></pre>

<h4>Role-Based Access in Views</h4>
<pre><code class="python">@login_required
def classrep_dashboard(request):
    \"\"\"Class rep dashboard - accessible only by class reps.\"\"\"
    rep_id = request.session.get("classrep_id")
    if not rep_id:
        return redirect("classrep_login")
    
    # Rest of implementation...</code></pre>

<h3>Form Management</h3>
<h4>User Form</h4>
<pre><code class="python">class UserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Leave empty to keep current password"
    )
    
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_active', 'groups']
        widgets = {
            'groups': forms.CheckboxSelectMultiple,
        }
    
    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This username is already taken.")
        return username</code></pre>

<h4>Group Form</h4>
<pre><code class="python">class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'})
        }
    
    def clean_name(self):
        name = self.cleaned_data['name']
        if Group.objects.filter(name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A group with this name already exists.")
        return name</code></pre>

<h3>CRUD Operations</h3>
<h4>Add Leader Role</h4>
<pre><code class="python">if request.POST.get("form_action") == "add_leader":
    uid = request.POST.get("user_id")
    role = request.POST.get("role")
    if uid and role:
        user = get_object_or_404(User, pk=uid)
        OrgRole.objects.create(user=user, title=role)</code></pre>

<h4>Reset User Password</h4>
<pre><code class="python">elif "reset_user" in request.POST:
    u = get_object_or_404(User, pk=request.POST.get("user_id"))
    new_pwd = request.POST.get("new_password") or f"{u.username}123"
    u.set_password(new_pwd)
    u.save()</code></pre>

<h4>Delete Group</h4>
<pre><code class="python">elif "delete_group" in request.POST:
    get_object_or_404(Group, pk=request.POST.get("group_id")).delete()</code></pre>

<h3>Session Management</h3>
<h4>Class Rep Sessions</h4>
<pre><code class="python"># Store in session on login
request.session["classrep_id"] = rep.id
request.session["classrep_username"] = rep.username

# Check in views
rep_id = request.session.get("classrep_id")
if not rep_id:
    return redirect("classrep_login")</code></pre>

<h4>Session Security</h4>
<pre><code class="python"># Django settings for secure sessions
SESSION_COOKIE_AGE = 1209600  # 2 weeks in seconds
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SECURE = True  # For HTTPS
SESSION_COOKIE_HTTPONLY = True</code></pre>

<h3>Use Cases</h3>
<h4>New User Onboarding</h4>
<ol>
<li>Superuser creates user account</li>
<li>Assign appropriate groups and roles</li>
<li>Set initial password</li>
<li>Notify user of account creation</li>
<li>User resets password on first login</li>
</ol>

<h4>Role Changes</h4>
<ol>
<li>Update user's groups and roles</li>
<li>Adjust permissions accordingly</li>
<li>Notify user of changes</li>
<li>Update any role-specific settings</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Principle of least privilege</strong>: Grant minimum necessary access</li>
<li><strong>Regular access reviews</strong>: Periodically review user permissions</li>
<li><strong>Strong password policies</strong>: Enforce password complexity</li>
<li><strong>Session timeout</strong>: Implement appropriate session durations</li>
<li><strong>Audit logging</strong>: Log all user management actions</li>
<li><strong>Two-factor authentication</strong>: Consider for admin accounts</li>
</ol>
"""

page6 = DocumentationPage.objects.create(
    title='User and Role Management System - Complete Guide',
    slug='user-role-management-system-complete-guide',
    short_description='Complete guide to user management, group management, and role-based access control',
    content=page6_content,
    category=categories['user-role-management'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=22,
    version='1.0'
)
print(f"   Created: {page6.title}")

# ============================================================
# 4. CREATE DETAILED SECTIONS WITH CODE EXAMPLES
# ============================================================

print("\n4. CREATING DETAILED SECTIONS WITH CODE EXAMPLES...")

# ============================================================
# SECTION 1: Complete Timetable View Implementation
# ============================================================

section1 = DocumentationSection.objects.create(
    page=page4,
    title='Complete Timetable View Implementation',
    content='''
<h3>Full Timetable View Implementation</h3>
<p>Complete implementation of the timetable_view function with all features and error handling.</p>
''',
    order=1,
    is_active=True,
    slug='complete-timetable-view-implementation'
)
print(f"   Created section: {section1.title}")

# Code Example 1: Complete Timetable View
code1 = CodeExample.objects.create(
    section=section1,
    title='Complete Timetable View Function',
    code='''from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.db import transaction
from django.db import models
from TT_APP.models import (
    Program, Department, Faculty, Timetable, ExamTimetable,
    ProgramCourse, ProgramCode, CourseAllocation,
    AutoMergedExamGroup, MergedCourseGroup, MinimalTimetable
)
import logging

logger = logging.getLogger(__name__)

@transaction.atomic
def timetable_view(request, program_id=None, timetable_type=None):
    """
    Handles both page load and AJAX timetable fetch:
    - Student selects Program + Year
    - Timetable returns only courses belonging to that academic year
      based on ProgramCourse.year
    """

    # --------------------------------------------
    # CASE 1: Show viewer page (no program_id or timetable_type)
    # --------------------------------------------
    if not program_id or not timetable_type:
        programs = Program.objects.select_related(
            "department__faculty"
        ).all().order_by("name")

        return render(request, "view_timetable.html", {"programs": programs})

    try:
        # Get program and year from request
        program = get_object_or_404(Program, id=program_id)
        year = int(request.GET.get("year") or 1)

        # -------------------------------------------------------------
        # STRICT YEAR FILTER based on ProgramCourse (CORE FIX)
        # -------------------------------------------------------------
        # Step 1: Get course codes for this program + selected year
        program_courses = ProgramCourse.objects.filter(
            program_id=program_id,
            year=year
        ).values_list("course_code", flat=True)

        # Step 2: Get CourseAllocation IDs matching those courses
        program_alloc_qs = CourseAllocation.objects.filter(
            program_id=program_id,
            course_code__in=program_courses
        )

        alloc_ids = list(program_alloc_qs.values_list("id", flat=True))

        # -------------------------------------------------------------
        # Support for merged courses (auto + manual)
        # -------------------------------------------------------------
        merged_base_ids = set()

        # Auto-merged exam groups
        auto_merged = AutoMergedExamGroup.objects.filter(
            merged_courses__in=program_alloc_qs
        ).values_list("base_course_id", flat=True)
        merged_base_ids.update(auto_merged)

        # Manual merged course groups
        manual_merged = MergedCourseGroup.objects.filter(
            merged_courses__in=program_alloc_qs
        ).values_list("base_course_id", flat=True)
        merged_base_ids.update(manual_merged)

        # Final filter for timetable entries
        base_q = (
            models.Q(course_allocation_id__in=alloc_ids)
            | models.Q(course_allocation_id__in=merged_base_ids)
        )

        # -------------------------------------------------------------
        # MINIMAL (Class Rep) TIMETABLE
        # -------------------------------------------------------------
        if timetable_type == "minimal":
            rep_entries = MinimalTimetable.objects.filter(
                class_rep__program_id=program_id,
                class_rep__year_of_study=year
            ).order_by("day", "start_time")

            minimal_data = {}
            for entry in rep_entries:
                day = entry.day
                minimal_data.setdefault(day, [])
                minimal_data[day].append({
                    "venue": entry.venue,
                    "course_code": entry.course_code,
                    "course_name": entry.course_name,
                    "lecturer": "Class Rep Entry",
                    "start": entry.start_time.strftime("%H:%M"),
                    "end": entry.end_time.strftime("%H:%M"),
                })

            return JsonResponse({
                "program_name": program.name,
                "year": year,
                "timetable_type": "minimal",
                "has_entries": rep_entries.exists(),
                "data": minimal_data,
            })

        # -------------------------------------------------------------
        # MAIN TIMETABLE
        # -------------------------------------------------------------
        if timetable_type == "main":
            entries = Timetable.objects.filter(base_q).select_related(
                "course_allocation", "course_allocation__lecturer"
            ).order_by("day", "start_time")

            main_data = {}
            for e in entries:
                ca = e.course_allocation
                day = e.day
                main_data.setdefault(day, [])
                main_data[day].append({
                    "venue": e.venue,
                    "course_code": ca.course_code,
                    "course_name": ca.course_name,
                    "lecturer": getattr(ca.lecturer, "display_name", "Unassigned"),
                    "start": e.start_time.strftime("%H:%M"),
                    "end": e.end_time.strftime("%H:%M"),
                })

            return JsonResponse({
                "program_name": program.name,
                "year": year,
                "timetable_type": "main",
                "has_entries": entries.exists(),
                "data": main_data,
            })

        # -------------------------------------------------------------
        # EXAM TIMETABLE
        # -------------------------------------------------------------
        if timetable_type == "exam":
            entries = ExamTimetable.objects.filter(base_q).select_related(
                "course_allocation", "course_allocation__lecturer"
            ).order_by("date", "start_time")

            exam_data = {}
            for e in entries:
                ca = e.course_allocation
                key = f"{e.date} ({e.day})"
                exam_data.setdefault(key, [])
                exam_data[key].append({
                    "venue": e.venue,
                    "course_code": ca.course_code,
                    "course_name": ca.course_name,
                    "lecturer": getattr(ca.lecturer, "display_name", "Unassigned"),
                    "start": e.start_time.strftime("%H:%M"),
                    "end": e.end_time.strftime("%H:%M"),
                })

            return JsonResponse({
                "program_name": program.name,
                "year": year,
                "timetable_type": "exam",
                "has_entries": entries.exists(),
                "data": exam_data,
            })

        # Invalid timetable type
        return JsonResponse({"error": "Invalid timetable type", "has_entries": False}, status=400)

    except ValueError as e:
        logger.error(f"Value error in timetable_view: {e}")
        return JsonResponse({"error": "Invalid year parameter", "has_entries": False}, status=400)
    
    except Exception as e:
        logger.error(f"Unexpected error in timetable_view: {e}", exc_info=True)
        return JsonResponse({"error": str(e), "has_entries": False}, status=500)


# URL Configuration
"""
from django.urls import path
from . import views

urlpatterns = [
    path('timetable/view/', views.timetable_view, name='timetable_view'),
    path('timetable/view/<int:program_id>/<str:timetable_type>/', 
         views.timetable_view, name='timetable_view_detail'),
]
"""

# HTML Template (view_timetable.html)
"""
{% extends "base.html" %}

{% block title %}View Timetable{% endblock %}

{% block content %}
<div class="container">
    <h1>View Timetable</h1>
    
    <div class="row">
        <div class="col-md-4">
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">Select Program</h5>
                </div>
                <div class="card-body">
                    <select id="programSelect" class="form-control">
                        <option value="">-- Select Program --</option>
                        {% for program in programs %}
                        <option value="{{ program.id }}">
                            {{ program.name }} - {{ program.department.faculty.code }}
                        </option>
                        {% endfor %}
                    </select>
                </div>
            </div>
        </div>
        
        <div class="col-md-3">
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">Academic Year</h5>
                </div>
                <div class="card-body">
                    <select id="yearSelect" class="form-control">
                        <option value="1">Year 1</option>
                        <option value="2">Year 2</option>
                        <option value="3">Year 3</option>
                        <option value="4">Year 4</option>
                        <option value="5">Year 5</option>
                    </select>
                </div>
            </div>
        </div>
        
        <div class="col-md-5">
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">Timetable Type</h5>
                </div>
                <div class="card-body">
                    <div class="btn-group btn-group-toggle" data-toggle="buttons">
                        <label class="btn btn-outline-primary active">
                            <input type="radio" name="timetableType" value="main" checked> Main Timetable
                        </label>
                        <label class="btn btn-outline-primary">
                            <input type="radio" name="timetableType" value="exam"> Exam Timetable
                        </label>
                        <label class="btn btn-outline-primary">
                            <input type="radio" name="timetableType" value="minimal"> Minimal Timetable
                        </label>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="mt-3">
        <button id="loadTimetable" class="btn btn-primary" disabled>
            <i class="fas fa-calendar-alt"></i> Load Timetable
        </button>
    </div>
    
    <div id="timetableContainer" class="mt-4"></div>
</div>

<script>
document.addEventListener('DOMContentLoaded', function() {
    const programSelect = document.getElementById('programSelect');
    const yearSelect = document.getElementById('yearSelect');
    const loadButton = document.getElementById('loadTimetable');
    const timetableContainer = document.getElementById('timetableContainer');
    
    // Enable load button when program is selected
    programSelect.addEventListener('change', function() {
        loadButton.disabled = !this.value;
    });
    
    // Load timetable button click
    loadButton.addEventListener('click', function() {
        const programId = programSelect.value;
        const year = yearSelect.value;
        const timetableType = document.querySelector('input[name="timetableType"]:checked').value;
        
        if (!programId) {
            alert('Please select a program');
            return;
        }
        
        // Show loading state
        loadButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
        loadButton.disabled = true;
        
        // Make AJAX request
        fetch(`/timetable/view/${programId}/${timetableType}/?year=${year}`)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.error) {
                    showError(data.error);
                } else {
                    displayTimetable(data);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showError('Failed to load timetable. Please try again.');
            })
            .finally(() => {
                // Reset button state
                loadButton.innerHTML = '<i class="fas fa-calendar-alt"></i> Load Timetable';
                loadButton.disabled = false;
            });
    });
    
    function displayTimetable(data) {
        let html = `
            <div class="card">
                <div class="card-header bg-primary text-white">
                    <h4 class="mb-0">
                        ${data.program_name} - Year ${data.year}
                        <span class="badge badge-light float-right">${data.timetable_type}</span>
                    </h4>
                </div>
                <div class="card-body">
        `;
        
        if (!data.has_entries) {
            html += `
                <div class="alert alert-info">
                    <i class="fas fa-info-circle"></i> No timetable entries found for this selection.
                </div>
            `;
        } else {
            // Create timetable grid
            const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
            
            html += '<div class="table-responsive">';
            html += '<table class="table table-bordered">';
            html += '<thead><tr>';
            
            // Table headers
            html += '<th>Time</th>';
            for (const day of days) {
                html += `<th>${day}</th>`;
            }
            html += '</tr></thead>';
            
            html += '<tbody>';
            
            // Generate time slots (simplified version)
            const timeSlots = [
                '08:00-10:00', '10:00-12:00', '12:00-14:00',
                '14:00-16:00', '16:00-18:00'
            ];
            
            for (const timeSlot of timeSlots) {
                html += '<tr>';
                html += `<td class="text-center">${timeSlot}</td>`;
                
                for (const day of days) {
                    const entries = data.data[day] || [];
                    const slotEntries = entries.filter(entry => 
                        entry.start === timeSlot.split('-')[0]
                    );
                    
                    html += '<td>';
                    if (slotEntries.length > 0) {
                        for (const entry of slotEntries) {
                            html += `
                                <div class="timetable-entry">
                                    <strong>${entry.course_code}</strong><br>
                                    <small>${entry.course_name}</small><br>
                                    <small>${entry.venue} - ${entry.lecturer}</small>
                                </div>
                            `;
                        }
                    }
                    html += '</td>';
                }
                
                html += '</tr>';
            }
            
            html += '</tbody>';
            html += '</table>';
            html += '</div>';
        }
        
        html += '</div></div>';
        timetableContainer.innerHTML = html;
    }
    
    function showError(message) {
        timetableContainer.innerHTML = `
            <div class="alert alert-danger alert-dismissible fade show" role="alert">
                <i class="fas fa-exclamation-triangle"></i> ${message}
                <button type="button" class="close" data-dismiss="alert" aria-label="Close">
                    <span aria-hidden="true">&times;</span>
                </button>
            </div>
        `;
    }
});
</script>

<style>
.timetable-entry {
    background-color: #f8f9fa;
    border-left: 4px solid #007bff;
    padding: 8px;
    margin-bottom: 4px;
    border-radius: 4px;
    font-size: 0.9em;
}

.timetable-entry:hover {
    background-color: #e9ecef;
    cursor: pointer;
}

.table th {
    background-color: #f8f9fa;
    text-align: center;
    font-weight: 600;
}

.table td {
    vertical-align: top;
    min-height: 80px;
}
</style>
{% endblock %}
"""''',
    language='python',
    order=1,
    description='Complete implementation of timetable_view with HTML template and JavaScript'
)

# ============================================================
# SECTION 2: Complete Class Rep Login System
# ============================================================

section2 = DocumentationSection.objects.create(
    page=page2,
    title='Complete Class Rep Login and Account Management',
    content='''
<h3>Full Class Rep Login System Implementation</h3>
<p>Complete implementation of class rep login, account creation, and session management.</p>
''',
    order=1,
    is_active=True,
    slug='complete-class-rep-login-system'
)
print(f"   Created section: {section2.title}")

# Code Example 2: Class Rep Login System
code2 = CodeExample.objects.create(
    section=section2,
    title='Complete Class Rep Login System',
    code='''from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.utils.crypto import get_random_string
from django.http import JsonResponse, HttpResponseBadRequest
from TT_APP.models import ClassRep, Program, ProgramCode, Timetable, MinimalTimetable
import logging

logger = logging.getLogger(__name__)

def generate_unique_reg_no(base_code: str, max_length: int = 20) -> str:
    """
    Generate a unique reg_no that doesn't already exist in ClassRep.
    
    Args:
        base_code: Base code from program name
        max_length: Maximum length of registration number
        
    Returns:
        str: Unique registration number
    """
    for _ in range(20):  # retry up to 20 times just in case
        random_suffix = get_random_string(3).upper()
        reg_no = f"REP-{base_code}{random_suffix}"[:max_length]
        if not ClassRep.objects.filter(reg_no=reg_no).exists():
            return reg_no
    # fallback (extremely rare)
    return f"REP-{get_random_string(6).upper()}"[:max_length]

def classrep_login(request):
    """
    Class Rep login + credential reset page.
    Each Program automatically has a ClassRep account created.
    """
    # --- Step 1: Auto-create ClassRep accounts for all programs ---
    with transaction.atomic():
        for program in Program.objects.all():
            # Skip if already linked to a ClassRep
            if ClassRep.objects.filter(program=program).exists():
                continue

            # Build short code from program name (first 3 initials)
            base_code = "".join(word[0].upper() for word in program.name.split()[:3])

            reg_no = generate_unique_reg_no(base_code, max_length=20)

            normalized = program.name.replace(" ", "").lower()
            username = normalized[:30]  # limit username length safely

            # Ensure no username duplicates
            if ClassRep.objects.filter(username=username).exists():
                username = f"{username}{get_random_string(2)}"

            ClassRep.objects.create(
                full_name=program.name,
                reg_no=reg_no,
                username=username,
                email=f"{username}@classrep.com",
                password=normalized,  # default = normalized program name
                program=program,
                active=True,
            )
            logger.info(f"Created ClassRep account for program: {program.name}")

    # --- Step 2: Handle reset credentials ---
    if request.method == "POST" and "reset" in request.POST:
        program_id = request.POST.get("program_id")
        if not program_id:
            messages.error(request, "Please select a program to reset credentials.")
        else:
            try:
                prog = get_object_or_404(Program, id=program_id)
                rep = get_object_or_404(ClassRep, program=prog)
                normalized = prog.name.replace(" ", "").lower()
                
                # Reset credentials
                rep.username = normalized
                rep.password = normalized
                rep.email = f"{normalized}@classrep.com"
                rep.save()
                
                messages.success(
                    request,
                    f"Credentials reset! Username and password are now '{normalized}' for {prog.name}."
                )
                logger.info(f"Reset credentials for ClassRep: {rep.username}")
                
            except Exception as e:
                messages.error(request, f"Error resetting credentials: {e}")
                logger.error(f"Error resetting ClassRep credentials: {e}")

    # --- Step 3: Handle login request ---
    elif request.method == "POST" and "login" in request.POST:
        username = request.POST.get("username", "").strip().lower()
        password = request.POST.get("password", "").strip()

        try:
            rep = ClassRep.objects.get(username=username)
            
            # Check password
            if rep.password == password:
                if not rep.active:
                    messages.error(request, "Your account is inactive. Contact the admin.")
                else:
                    # Set session variables
                    request.session["classrep_id"] = rep.id
                    request.session["classrep_username"] = rep.username
                    request.session["classrep_program"] = rep.program.id
                    
                    # Update last login
                    rep.last_login = timezone.now()
                    rep.save()
                    
                    logger.info(f"ClassRep logged in: {rep.username}")
                    return redirect("classrep_dashboard")
            else:
                messages.error(request, "Invalid password.")
                logger.warning(f"Failed login attempt for ClassRep: {username}")
                
        except ClassRep.DoesNotExist:
            messages.error(request, "No ClassRep account found with that username.")
            logger.warning(f"ClassRep login attempt with non-existent username: {username}")
        except Exception as e:
            messages.error(request, "An error occurred during login.")
            logger.error(f"Error during ClassRep login: {e}")

    # --- Step 4: Render login page ---
    programs = Program.objects.all().order_by("name")
    return render(request, "classrep_login.html", {"programs": programs})


@transaction.atomic
def classrep_dashboard(request):
    """
    Main dashboard for class representatives.
    """
    # Check authentication
    rep_id = request.session.get("classrep_id")
    if not rep_id:
        messages.error(request, "Please login to access the dashboard.")
        return redirect("classrep_login")

    try:
        rep = get_object_or_404(ClassRep, id=rep_id)
        
        # Get dashboard data
        notifications = rep.notifications.all().order_by("-created_at")[:10]
        timetable = Timetable.objects.filter(course_allocation__program=rep.program)
        minimal = MinimalTimetable.objects.filter(class_rep=rep).order_by("day", "start_time")
        programs = Program.objects.all().order_by("name")

        # ✅ Handle AJAX submission for adding program codes
        if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
            program_id = request.POST.get("program_id")
            code = (request.POST.get("program_code") or "").strip().upper()

            if not (program_id and code):
                return HttpResponseBadRequest("Missing program or code")

            program = get_object_or_404(Program, id=program_id)
            program_code, created = ProgramCode.objects.get_or_create(
                program=program, 
                code=code
            )

            return JsonResponse({
                "success": True,
                "created": created,
                "program": program.name,
                "code": program_code.code,
                "message": "✅ Code added successfully!" if created else "⚠️ This code already exists for this program."
            })

        # ✅ Normal GET rendering
        programscode = ProgramCode.objects.select_related("program").all().order_by("program__name")

        # Prepare structured data for table
        table_data = []
        for prog in programs:
            codes = list(prog.program_codes.values_list("code", flat=True))
            table_data.append({
                "program": prog.name,
                "codes": ", ".join(codes) if codes else "—"
            })

        context = {
            "rep": rep,
            "notifications": notifications,
            "timetable": timetable,
            "minimal": minimal,
            "programs": programs,
            "programs_codes": table_data,
            "dashboard_title": f"{rep.program.name} - Class Rep Dashboard",
        }

        return render(request, "classrep_dashboard.html", context)

    except Exception as e:
        logger.error(f"Error loading classrep dashboard: {e}")
        messages.error(request, "Error loading dashboard. Please try again.")
        return redirect("classrep_login")


def publish_minimal_timetable(request):
    """
    Copy main timetable to minimal timetable for class rep.
    """
    rep_id = request.session.get("classrep_id")
    if not rep_id:
        messages.error(request, "Please login to perform this action.")
        return redirect("classrep_login")

    try:
        rep = ClassRep.objects.get(id=rep_id)
        
        # Get timetable entries for this program
        timetable_entries = Timetable.objects.filter(
            course_allocation__program=rep.program
        ).select_related("course_allocation")
        
        # Delete old entries
        deleted_count, _ = MinimalTimetable.objects.filter(class_rep=rep).delete()
        
        # Create minimal copy
        created_count = 0
        for t in timetable_entries:
            MinimalTimetable.objects.create(
                class_rep=rep,
                course_code=t.course_allocation.course_code,
                course_name=t.course_allocation.course_name,
                day=t.day,
                start_time=t.start_time,
                end_time=t.end_time,
                venue=t.venue,
                created_at=timezone.now(),
            )
            created_count += 1

        messages.success(
            request, 
            f"Timetable published successfully! {created_count} entries copied to Minimal Timetable."
        )
        logger.info(f"ClassRep {rep.username} published minimal timetable: {created_count} entries")
        
    except Exception as e:
        messages.error(request, f"Error publishing timetable: {e}")
        logger.error(f"Error publishing minimal timetable: {e}")

    return redirect("classrep_dashboard")


def edit_minimal_entry(request, entry_id):
    """
    Edit individual minimal timetable entry.
    """
    rep_id = request.session.get("classrep_id")
    if not rep_id:
        messages.error(request, "Please login to edit entries.")
        return redirect("classrep_login")

    try:
        entry = get_object_or_404(MinimalTimetable, id=entry_id, class_rep_id=rep_id)

        if request.method == "POST":
            # Validate form data
            day = request.POST.get("day")
            start_time = request.POST.get("start_time")
            end_time = request.POST.get("end_time")
            venue = request.POST.get("venue", "").strip()

            if not all([day, start_time, end_time, venue]):
                messages.error(request, "All fields are required.")
            else:
                entry.day = day
                entry.start_time = start_time
                entry.end_time = end_time
                entry.venue = venue
                entry.updated_at = timezone.now()
                entry.save()
                
                messages.success(request, "Entry updated successfully.")
                logger.info(f"ClassRep updated minimal timetable entry: {entry_id}")
                return redirect("classrep_dashboard")

        # Available days for dropdown
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        
        context = {
            "entry": entry,
            "days": days,
            "time_slots": [
                ("08:00", "08:00 AM"), ("09:00", "09:00 AM"), ("10:00", "10:00 AM"),
                ("11:00", "11:00 AM"), ("12:00", "12:00 PM"), ("13:00", "01:00 PM"),
                ("14:00", "02:00 PM"), ("15:00", "03:00 PM"), ("16:00", "04:00 PM"),
                ("17:00", "05:00 PM"), ("18:00", "06:00 PM")
            ]
        }
        
        return render(request, "classrep_edit_entry.html", context)

    except Exception as e:
        messages.error(request, f"Error editing entry: {e}")
        logger.error(f"Error editing minimal timetable entry: {e}")
        return redirect("classrep_dashboard")


def classrep_logout(request):
    """
    Logout class representative and clear session.
    """
    if "classrep_id" in request.session:
        username = request.session.get("classrep_username", "Unknown")
        request.session.flush()
        logger.info(f"ClassRep logged out: {username}")
        messages.success(request, "You have been logged out successfully.")
    
    return redirect("classrep_login")


# URL Configuration
"""
from django.urls import path
from . import views

urlpatterns = [
    path('classrep/login/', views.classrep_login, name='classrep_login'),
    path('classrep/logout/', views.classrep_logout, name='classrep_logout'),
    path('classrep/dashboard/', views.classrep_dashboard, name='classrep_dashboard'),
    path('classrep/publish/', views.publish_minimal_timetable, name='publish_minimal_timetable'),
    path('classrep/edit/<int:entry_id>/', views.edit_minimal_entry, name='edit_minimal_entry'),
]
"""

# ClassRep Model
"""
from django.db import models
from django.utils import timezone

class ClassRep(models.Model):
    full_name = models.CharField(max_length=200)
    reg_no = models.CharField(max_length=50, unique=True)
    username = models.CharField(max_length=100, unique=True)
    email = models.EmailField()
    password = models.CharField(max_length=100)  # Consider hashing in production
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name='class_reps')
    active = models.BooleanField(default=True)
    last_login = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Class Representative"
        verbose_name_plural = "Class Representatives"
        ordering = ['program__name', 'username']
    
    def __str__(self):
        return f"{self.full_name} ({self.username}) - {self.program.name}"
    
    def is_active(self):
        return self.active
    
    def get_display_name(self):
        return self.full_name or self.username
"""

# MinimalTimetable Model
"""
class MinimalTimetable(models.Model):
    class_rep = models.ForeignKey(ClassRep, on_delete=models.CASCADE, related_name='minimal_timetables')
    course_code = models.CharField(max_length=50)
    course_name = models.CharField(max_length=200)
    day = models.CharField(max_length=20)
    start_time = models.TimeField()
    end_time = models.TimeField()
    venue = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['day', 'start_time']
        verbose_name = "Minimal Timetable Entry"
        verbose_name_plural = "Minimal Timetable Entries"
    
    def __str__(self):
        return f"{self.course_code} - {self.day} {self.start_time}"
    
    def get_duration(self):
        start = datetime.combine(datetime.today(), self.start_time)
        end = datetime.combine(datetime.today(), self.end_time)
        duration = end - start
        hours = duration.seconds // 3600
        minutes = (duration.seconds % 3600) // 60
        return f"{hours}h {minutes}m"
"""''',
    language='python',
    order=1,
    description='Complete class rep login system with dashboard and minimal timetable management'
)

# ============================================================
# 5. ASSIGN TAGS TO PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO PAGES...")

# Define tag assignments
tag_assignments = {
    page1: ['student-portal', 'timetable', 'json-api', 'views'],
    page2: ['class-rep', 'authentication', 'timetable', 'database'],
    page3: ['superuser', 'dvc', 'admin', 'user-management', 'role-management'],
    page4: ['timetable', 'program-management', 'course-allocation', 'merged-courses'],
    page5: ['program-management', 'faculty', 'department', 'course-allocation'],
    page6: ['user-management', 'role-management', 'authentication', 'admin'],
}

for page, tag_slugs in tag_assignments.items():
    for tag_slug in tag_slugs:
        if tag_slug in tags:
            tag = tags[tag_slug]
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
    (page1, page4, "Student portal uses timetable management system"),
    (page2, page4, "Class reps use minimal timetable from main system"),
    (page2, page6, "Class reps are part of user management system"),
    (page3, page6, "Superuser admin manages users and roles"),
    (page4, page5, "Timetable management uses program and course data"),
    (page5, page6, "Program management includes user role assignment"),
    (page4, page2, "Minimal timetable is managed by class reps"),
    (page6, page3, "User management includes superuser functions"),
]

for from_page, to_page, description in internal_links:
    InternalLink.objects.get_or_create(
        from_page=from_page,
        to_page=to_page,
        defaults={'description': description}
    )
    print(f"   Linked: {from_page.title} → {to_page.title}")

# ============================================================
# 7. CREATE ADDITIONAL PAGES FOR SPECIFIC FUNCTIONALITY
# ============================================================

print("\n7. CREATING ADDITIONAL SPECIALIZED PAGES...")

# Page 7: Merged Course Groups
page7_content = """
<h2>Merged Course Groups System</h2>
<p>System for managing auto-merged and manually merged course groups for efficient scheduling.</p>

<h3>Overview</h3>
<p>The Merged Course Groups System provides:</p>
<ol>
<li><strong>Auto-Merged Exam Groups</strong>: Automatically merged courses for exams</li>
<li><strong>Manual Course Groups</strong>: Manually created course groups</li>
<li><strong>Base Course Management</strong>: Management of base courses for merged groups</li>
<li><strong>Timetable Integration</strong>: Integration with main timetable system</li>
</ol>

<h3>Core Models</h3>
<h4>AutoMergedExamGroup Model</h4>
<pre><code class="python">class AutoMergedExamGroup(models.Model):
    \"\"\"Automatically merged exam groups for scheduling efficiency.\"\"\"
    base_course = models.ForeignKey(
        CourseAllocation, 
        on_delete=models.CASCADE,
        related_name='auto_merged_as_base'
    )
    merged_courses = models.ManyToManyField(
        CourseAllocation,
        related_name='auto_merged_in_groups'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Auto Merged Exam Group"
        verbose_name_plural = "Auto Merged Exam Groups"
    
    def __str__(self):
        return f"Auto Group: {self.base_course.course_code}"
    
    def get_merged_course_count(self):
        return self.merged_courses.count()</code></pre>

<h4>MergedCourseGroup Model</h4>
<pre><code class="python">class MergedCourseGroup(models.Model):
    \"\"\"Manually created course groups for scheduling.\"\"\"
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    base_course = models.ForeignKey(
        CourseAllocation,
        on_delete=models.CASCADE,
        related_name='manual_merged_as_base'
    )
    merged_courses = models.ManyToManyField(
        CourseAllocation,
        related_name='manual_merged_in_groups'
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Merged Course Group"
        verbose_name_plural = "Merged Course Groups"
    
    def __str__(self):
        return f"{self.name} ({self.base_course.course_code})"
    
    def get_total_students(self):
        \"\"\"Calculate total students in merged group.\"\"\"
        total = self.base_course.number_of_students or 0
        for course in self.merged_courses.all():
            total += course.number_of_students or 0
        return total</code></pre>

<h3>Integration with Timetable View</h3>
<pre><code class="python"># In timetable_view function:
# Support for merged courses (auto + manual)
merged_base_ids = set()

# Auto-merged exam groups
auto_merged = AutoMergedExamGroup.objects.filter(
    merged_courses__in=program_alloc_qs
).values_list("base_course_id", flat=True)
merged_base_ids.update(auto_merged)

# Manual merged course groups
manual_merged = MergedCourseGroup.objects.filter(
    merged_courses__in=program_alloc_qs
).values_list("base_course_id", flat=True)
merged_base_ids.update(manual_merged)

# Final filter for timetable entries
base_q = (
    models.Q(course_allocation_id__in=alloc_ids)
    | models.Q(course_allocation_id__in=merged_base_ids)
)</code></pre>

<h3>Use Cases</h3>
<h4>Exam Scheduling</h4>
<ol>
<li>System auto-merges courses with same lecturer/time</li>
<li>Exams scheduled for merged groups</li>
<li>Students from all merged courses attend same exam</li>
<li>Reduces number of exam sessions needed</li>
</ol>

<h4>Course Grouping</h4>
<ol>
<li>Admin creates manual course groups</li>
<li>Groups courses with similar content</li>
<li>Schedules groups together for efficiency</li>
<li>Manages room allocation for groups</li>
</ol>
"""

page7 = DocumentationPage.objects.create(
    title='Merged Course Groups System',
    slug='merged-course-groups-system',
    short_description='System for managing auto-merged and manually merged course groups',
    content=page7_content,
    category=categories['merged-course-groups'],
    page_type='reference',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=15,
    version='1.0'
)
print(f"   Created: {page7.title}")

# Page 8: Exam Timetable System
page8_content = """
<h2>Exam Timetable System</h2>
<p>Specialized system for exam scheduling with date-based management and constraints.</p>

<h3>Overview</h3>
<p>The Exam Timetable System provides:</p>
<ol>
<li><strong>Date-based Scheduling</strong>: Exams scheduled on specific dates</li>
<li><strong>Exam Constraints</strong>: Special constraints for exams</li>
<li><strong>Conflict Detection</strong>: Comprehensive conflict checking</li>
<li><strong>Room Allocation</strong>: Specialized room allocation for exams</li>
</ol>

<h3>Core Model</h3>
<pre><code class="python">class ExamTimetable(models.Model):
    \"\"\"Exam timetable entries with date-based scheduling.\"\"\"
    course_allocation = models.ForeignKey(
        CourseAllocation, 
        on_delete=models.CASCADE,
        related_name='exam_timetables'
    )
    date = models.DateField()
    day = models.CharField(max_length=20)
    start_time = models.TimeField()
    end_time = models.TimeField()
    venue = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['date', 'start_time']
        verbose_name = "Exam Timetable Entry"
        verbose_name_plural = "Exam Timetable Entries"
        indexes = [
            models.Index(fields=['date', 'start_time']),
            models.Index(fields=['course_allocation', 'date']),
            models.Index(fields=['venue', 'date']),
        ]
    
    def __str__(self):
        return f"{self.course_allocation.course_code} - {self.date} {self.start_time}"
    
    def get_duration(self):
        \"\"\"Calculate exam duration in hours.\"\"\"
        start = datetime.combine(self.date, self.start_time)
        end = datetime.combine(self.date, self.end_time)
        duration = end - start
        return duration.total_seconds() / 3600</code></pre>

<h3>Integration with Main System</h3>
<pre><code class="python"># In timetable_view function for exam timetable:
if timetable_type == "exam":
    entries = ExamTimetable.objects.filter(base_q).select_related(
        "course_allocation", "course_allocation__lecturer"
    ).order_by("date", "start_time")

    exam_data = {}
    for e in entries:
        ca = e.course_allocation
        key = f"{e.date} ({e.day})"
        exam_data.setdefault(key, [])
        exam_data[key].append({
            "venue": e.venue,
            "course_code": ca.course_code,
            "course_name": ca.course_name,
            "lecturer": getattr(ca.lecturer, "display_name", "Unassigned"),
            "start": e.start_time.strftime("%H:%M"),
            "end": e.end_time.strftime("%H:%M"),
        })</code></pre>

<h3>Constraint Types for Exams</h3>
<h4>Hard Constraints</h4>
<ol>
<li><strong>No overlapping exams</strong>: Students can't have two exams at same time</li>
<li><strong>Lecturer availability</strong>: Lecturers can't supervise multiple exams</li>
<li><strong>Room capacity</strong>: Exam room must accommodate all students</li>
<li><strong>Time limits</strong>: Exams within allowed time slots</li>
</ol>

<h4>Soft Constraints</h4>
<ol>
<li><strong>Preferred exam periods</strong>: Morning vs afternoon preferences</li>
<li><strong>Study time</strong>: Minimum gap between exams</li>
<li><strong>Special needs</strong>: Accommodations for special needs</li>
<li><strong>Room equipment</strong>: Special equipment requirements</li>
</ol>
"""

page8 = DocumentationPage.objects.create(
    title='Exam Timetable System',
    slug='exam-timetable-system',
    short_description='Specialized system for exam scheduling with date-based management',
    content=page8_content,
    category=categories['exam-timetable-system'],
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
print(f"   Created: {page8.title}")

# ============================================================
# 8. SUMMARY
# ============================================================

print("\n" + "=" * 80)
print("TIMETABLING SYSTEM DOCUMENTATION COMPLETE")
print("=" * 80)

# Count statistics
total_pages = DocumentationPage.objects.count()
total_sections = DocumentationSection.objects.count()
total_code_examples = CodeExample.objects.count()
total_links = InternalLink.objects.count()
total_categories = DocumentationCategory.objects.count()
total_tags = DocumentationTag.objects.count()

print(f"""
📊 DOCUMENTATION STATISTICS:
   Total Categories:       {total_categories}
   Total Pages:            {total_pages}
   Total Sections:         {total_sections}
   Total Code Examples:    {total_code_examples}
   Total Tags:             {total_tags}
   Total Internal Links:   {total_links}

📚 COMPREHENSIVE SYSTEM DOCUMENTATION:
   1. Student Portal System - Complete Guide
   2. Class Representative System - Complete Guide
   3. Superuser Administration System - Complete Guide
   4. Timetable Management System - Complete Guide
   5. Program and Course Management System - Complete Guide
   6. User and Role Management System - Complete Guide
   7. Merged Course Groups System
   8. Exam Timetable System

🔧 SYSTEM ARCHITECTURE DOCUMENTED:
   • Multi-user role-based access control
   • Academic hierarchy (Faculty → Department → Program → Course)
   • Timetable generation with constraints
   • Exam scheduling with special rules
   • Class representative management
   • Superuser administration interfaces
   • Database models and relationships
   • AJAX-based frontend interactions

💻 TECHNICAL IMPLEMENTATION:
   • Django views with transaction safety
   • Efficient database queries with select_related
   • JSON API endpoints for AJAX operations
   • Session-based authentication
   • Form validation and error handling
   • Comprehensive logging
   • Template inheritance and reuse
   • JavaScript integration for dynamic content

🔗 SYSTEM INTEGRATION POINTS:
   • Student portal integrates with timetable viewing
   • Class reps manage minimal timetables
   • Superusers configure system-wide settings
   • Program management feeds into timetable generation
   • User management controls access throughout system
   • Merged courses optimize scheduling efficiency
   • Exam system coordinates with regular timetable

👥 USER ROLES DOCUMENTED:
   • Students: View timetables and exam schedules
   • Class Representatives: Manage minimal timetables
   • Lecturers: Course allocation and viewing
   • Department Chairs (COD): Department management
   • Faculty Deans: Faculty oversight
   • Timetable Admins: System configuration
   • Timetable Director: Overall management
   • DVC: Executive oversight
   • Superuser: Full system administration

🚀 ACCESS POINTS:
   Student Portal:          /studentportal/
   Class Rep Login:         /classrep/login/
   Superuser Dashboard:     /sudo/dashboard/
   Timetable View:          /timetable/view/
   User Management:         /sudo/manage/accounts/

🎯 KEY WORKFLOWS DOCUMENTED:
   1. Student timetable viewing workflow
   2. Class rep account creation and management
   3. Superuser system configuration
   4. Timetable creation and constraint checking
   5. Program and course setup
   6. User role assignment and management
   7. Exam scheduling with merged courses

✅ BEST PRACTIVES IMPLEMENTED:
   • Atomic transactions for data consistency
   • Efficient database queries with indexing
   • Comprehensive error handling and logging
   • Secure session management
   • Input validation and sanitization
   • Role-based access control
   • Responsive frontend design
   • Regular backup and recovery procedures

📈 PERFORMANCE OPTIMIZATIONS:
   • Database indexing on frequently queried fields
   • select_related and prefetch_related for related objects
   • Query optimization with values_list and only()
   • Caching strategies for static data
   • Pagination for large result sets
   • Lazy loading for improved initial page load

🔒 SECURITY FEATURES:
   • CSRF protection on all forms
   • SQL injection prevention via Django ORM
   • XSS protection through template auto-escaping
   • Session security with timeout and encryption
   • Password hashing (where implemented)
   • Role-based permission checking
   • Input validation and sanitization

✅ Comprehensive documentation successfully created for the entire timetabling system!
""")

print("=" * 80)
print("NEXT STEPS:")
print("1. Review documentation at /documentation/")
print("2. Test the implemented systems")
print("3. Train users on their respective roles")
print("4. Monitor system performance and usage")
print("5. Update documentation as system evolves")
print("=" * 80)