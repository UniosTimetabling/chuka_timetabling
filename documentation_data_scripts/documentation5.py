#!/usr/bin/env python
"""
DEAN, LECTURE ALLOCATOR, AND DVC PANEL DOCUMENTATION
This script creates comprehensive documentation for dean panel, lecture allocator,
and DVC panel functionality in the timetabling system.
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
print("DEAN, LECTURE ALLOCATOR, AND DVC PANEL DOCUMENTATION")
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
        'name': 'Dean Panel & Faculty Management',
        'slug': 'dean-panel-faculty-management',
        'description': 'Dean panel operations for managing faculties and departments',
        'icon': 'fas fa-user-graduate',
        'order': 24,
        'access_level': 'management',
    },
    {
        'name': 'Lecture Allocator Panel',
        'slug': 'lecture-allocator-panel',
        'description': 'Course allocation editing and lecturer assignment',
        'icon': 'fas fa-chalkboard-teacher',
        'order': 25,
        'access_level': 'department',
    },
    {
        'name': 'DVC Panel & Approval Workflow',
        'slug': 'dvc-panel-approval-workflow',
        'description': 'Deputy Vice Chancellor panel for approving course allocations',
        'icon': 'fas fa-user-check',
        'order': 26,
        'access_level': 'management',
    },
    {
        'name': 'User Account Management',
        'slug': 'user-account-management',
        'description': 'Automated user creation and role assignment',
        'icon': 'fas fa-users-cog',
        'order': 27,
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
    {'name': 'Dean Panel', 'slug': 'dean-panel', 'color': '#28a745'},
    {'name': 'Faculty Management', 'slug': 'faculty-management', 'color': '#6f42c1'},
    {'name': 'Department Management', 'slug': 'department-management', 'color': '#17a2b8'},
    {'name': 'Lecture Allocator', 'slug': 'lecture-allocator', 'color': '#fd7e14'},
    {'name': 'DVC Panel', 'slug': 'dvc-panel', 'color': '#e83e8c'},
    {'name': 'Approval Workflow', 'slug': 'approval-workflow', 'color': '#20c997'},
    {'name': 'User Creation', 'slug': 'user-creation', 'color': '#6c757d'},
    {'name': 'Role Management', 'slug': 'role-management', 'color': '#6610f2'},
    {'name': 'Course Allocation', 'slug': 'course-allocation', 'color': '#dc3545'},
    {'name': 'Authentication', 'slug': 'authentication', 'color': '#ffc107'},
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

# Page 1: Dean Panel
page1_content = """
<h2>Dean Panel - Faculty and Department Management</h2>
<p>The Dean Panel provides comprehensive faculty and department management capabilities for Deans and Dean Admins.</p>

<h3>Overview</h3>
<p>The Dean Panel allows authorized users to:</p>
<ol>
<li>View departments within their faculty</li>
<li>Create new departments with automated COD account creation</li>
<li>Edit existing department details</li>
<li>Delete departments</li>
<li>Manage department leadership assignments</li>
</ol>

<h3>Access Control</h3>
<h4>Required Permissions</h4>
<ul>
<li><strong>User Groups</strong>: "Dean" or "Dean Admins"</li>
<li><strong>Faculty Assignment</strong>: User must be assigned as leader of a faculty</li>
<li><strong>Scope Limitation</strong>: Can only manage departments within assigned faculty</li>
</ul>

<h4>Permission Decorators</h4>
<pre><code class="python">@login_required
@group_required("Dean", "Dean Admins")
@transaction.atomic
def dean_panel(request):
    # Implementation
    pass</code></pre>

<h3>Faculty Detection</h3>
<p>The system detects the dean's assigned faculty:</p>
<pre><code class="python">try:
    dean_faculty = Faculty.objects.get(leader=request.user)
except Faculty.DoesNotExist:
    dean_faculty = None</code></pre>

<h3>Department Creation Workflow</h3>
<h4>Process Flow</h4>
<ol>
<li>Validate department name and faculty selection</li>
<li>Check security: ensure faculty belongs to dean</li>
<li>Handle leader option (new COD, existing user, or none)</li>
<li>Create department record</li>
<li>If new COD selected: create COD and COD Admin accounts</li>
<li>Return success response with credentials</li>
</ol>

<h4>Leader Options</h4>
<ul>
<li><strong>new</strong>: Create new COD and COD Admin accounts</li>
<li><strong>none</strong>: No department leader assigned</li>
<li><strong>user ID</strong>: Assign existing user as department leader</li>
</ul>

<h3>Automated User Creation</h3>
<h4>COD and COD Admin Creation</h4>
<pre><code class="python">def create_cod_and_admin(department_name: str):
    \"\"\"
    Create COD and COD Admin accounts for a department.
    Only set password when user is newly created.
    \"\"\"
    base_name = normalize_name(department_name)
    
    # COD account
    cod_username = f"cod_{base_name}"
    cod_password = f"{cod_username}@2025"
    cod_user, created = User.objects.get_or_create(username=cod_username)
    
    if created:
        cod_user.set_password(cod_password)
        cod_user.first_name = "COD"
        cod_user.last_name = department_name
        cod_user.is_active = True
        cod_user.save()
        OrgRole.objects.create(title="COD", user=cod_user)
    else:
        cod_password = None  # don't reset password if user already exists
    
    # Assign to COD group
    cod_group, _ = Group.objects.get_or_create(name="COD")
    if cod_group not in cod_user.groups.all():
        cod_user.groups.add(cod_group)
    
    # COD Admin account (similar logic)
    # ...
    
    return cod_user, admin_user, cod_password, admin_password</code></pre>

<h3>Security Considerations</h3>
<h4>Faculty Scope Enforcement</h4>
<p>The system enforces that deans can only manage departments within their assigned faculty:</p>
<pre><code class="python"># Security check: only allow dean's faculty
faculty = get_object_or_404(Faculty, id=faculty_id)
if faculty != dean_faculty:
    return JsonResponse({
        "status": "error",
        "message": "You can only manage your own faculty.",
        "popup": {"type": "error", "message": "You can only manage your own faculty."}
    }, status=403)</code></pre>

<h4>Transaction Safety</h4>
<p>All database operations are wrapped in atomic transactions:</p>
<pre><code class="python">@transaction.atomic
def dean_panel(request):
    # All operations are atomic
    pass</code></pre>

<h3>Error Handling</h3>
<h4>Common Error Scenarios</h4>
<ul>
<li><strong>No faculty assignment</strong>: User not assigned as faculty leader</li>
<li><strong>Invalid faculty</strong>: Attempt to manage other faculty's departments</li>
<li><strong>Missing required fields</strong>: Department name or faculty not provided</li>
<li><strong>Duplicate department</strong>: Department with same name already exists</li>
<li><strong>Database integrity errors</strong>: Constraint violations</li>
</ul>

<h4>Error Response Format</h4>
<pre><code class="python">return JsonResponse({
    "status": "error",
    "message": "Error message for logs",
    "popup": {"type": "error", "message": "User-friendly error message"}
}, status=400)</code></pre>

<h3>AJAX Endpoints</h3>
<h4>Create Department</h4>
<p><strong>Endpoint</strong>: POST with action="create"</p>
<p><strong>Parameters</strong>:</p>
<ul>
<li>name: Department name (required)</li>
<li>description: Department description</li>
<li>faculty_id: Faculty ID (required)</li>
<li>leader_option: "new", "none", or user ID</li>
</ul>

<h4>Edit Department</h4>
<p><strong>Endpoint</strong>: POST with action="edit"</p>
<p><strong>Parameters</strong>:</p>
<ul>
<li>id: Department ID (required)</li>
<li>name: New department name (required)</li>
<li>description: New description</li>
<li>faculty_id: New faculty ID</li>
<li>leader_option: Leader selection option</li>
</ul>

<h4>Delete Department</h4>
<p><strong>Endpoint</strong>: POST with action="delete"</p>
<p><strong>Parameters</strong>:</p>
<ul>
<li>id: Department ID to delete (required)</li>
</ul>

<h3>Template Context</h3>
<p>For GET requests, the view provides:</p>
<pre><code class="python">return render(request, "dean_panel.html", {
    "faculties": faculties,        # List containing dean's faculty
    "departments": departments,    # Departments in dean's faculty
    "users": users                 # All users for leader selection
})</code></pre>

<h3>Use Cases</h3>
<h4>New Academic Year Setup</h4>
<ol>
<li>Create new departments for new programs</li>
<li>Assign COD leadership</li>
<li>Generate credentials for new COD teams</li>
</ol>

<h4>Department Reorganization</h4>
<ol>
<li>Edit department names and descriptions</li>
<li>Reassign department leadership</li>
<li>Move departments between faculties (if permitted)</li>
</ol>

<h4>Department Consolidation</h4>
<ol>
<li>Delete obsolete departments</li>
<li>Reassign courses and allocations</li>
<li>Archive historical data</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Validate thoroughly</strong>: Always validate faculty ownership</li>
<li><strong>Use transactions</strong>: Ensure data consistency</li>
<li><strong>Secure credentials</strong>: Handle generated passwords carefully</li>
<li><strong>Communicate changes</strong>: Inform affected users of department changes</li>
<li><strong>Archive before deletion</strong>: Archive data before deleting departments</li>
<li><strong>Regular audits</strong>: Periodically review department structure</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>COD Panel</strong>: Created COD accounts can access COD panel</li>
<li><strong>Course Allocation</strong>: Departments appear in course allocation forms</li>
<li><strong>Program Management</strong>: Departments can create programs</li>
<li><strong>Reporting</strong>: Department structure affects reporting hierarchies</li>
</ul>
"""

page1 = DocumentationPage.objects.create(
    title='Dean Panel - Faculty and Department Management',
    slug='dean-panel-faculty-department-management',
    short_description='Complete guide to managing faculties and departments as a Dean',
    content=page1_content,
    category=categories['dean-panel-faculty-management'],
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
print(f"   Created: {page1.title}")

# Page 2: Lecture Allocator Panel
page2_content = """
<h2>Lecture Allocator Panel - Course Allocation Editing</h2>
<p>The Lecture Allocator Panel allows CODs to edit course allocations within their department.</p>

<h3>Overview</h3>
<p>The Lecture Allocator Panel provides:</p>
<ol>
<li>View all course allocations for the department</li>
<li>Edit course details (code, name)</li>
<li>Assign or change lecturers for courses</li>
<li>Create new lecturer profiles if needed</li>
<li>Simple, focused interface for allocation management</li>
</ol>

<h3>Access Control</h3>
<h4>Required Permissions</h4>
<ul>
<li><strong>Authentication</strong>: User must be logged in</li>
<li><strong>Department Detection</strong>: User must be associated with a department</li>
<li><strong>Scope Limitation</strong>: Can only see and edit allocations in their department</li>
</ul>

<h4>Permission Decorator</h4>
<pre><code class="python">@login_required
def lecture_allocator_panel(request):
    # Implementation
    pass</code></pre>

<h3>Department Detection</h3>
<p>The system uses the same department detection as the COD panel:</p>
<pre><code class="python">dept = detect_user_department(user)
if not dept:
    messages.error(request, "Your department could not be detected. Contact Admin.")
    return redirect("cod_panel")</code></pre>

<h3>Data Loading</h3>
<h4>Allocations Query</h4>
<p>Load allocations with efficient related object fetching:</p>
<pre><code class="python">allocations = (
    CourseAllocation.objects.filter(department=dept)
    .select_related("origin_department", "lecturer", "program")
    .order_by("course_code")
)</code></pre>

<h4>Lecturers List</h4>
<p>Load all lecturers for assignment dropdown:</p>
<pre><code class="python">lecturers = Lecturer.objects.all().order_by("name")</code></pre>

<h3>Update Process</h3>
<h4>Update Workflow</h4>
<ol>
<li>Get allocation ID from POST data</li>
<li>Verify allocation belongs to user's department</li>
<li>Update course code and name if provided</li>
<li>Handle lecturer assignment (existing or new)</li>
<li>Save changes and display success message</li>
</ol>

<h4>Update Implementation</h4>
<pre><code class="python">if request.method == "POST":
    alloc_id = request.POST.get("id")
    alloc = get_object_or_404(CourseAllocation, id=alloc_id, department=dept)

    alloc.course_code = request.POST.get("course_code", alloc.course_code)
    alloc.course_name = request.POST.get("course_name", alloc.course_name)

    lecturer_id = request.POST.get("lecturer_id")
    if lecturer_id:
        if lecturer_id == "__new__":
            messages.info(request, "Redirecting to Lecturer Panel to add new lecturer.")
            return redirect("lecturer_panel")
        alloc.lecturer_id = lecturer_id or None

    alloc.save()
    messages.success(request, f"✅ {alloc.course_code} updated successfully.")</code></pre>

<h3>New Lecturer Creation</h3>
<p>If a new lecturer needs to be created:</p>
<pre><code class="python">if lecturer_id == "__new__":
    messages.info(request, "Redirecting to Lecturer Panel to add new lecturer.")
    return redirect("lecturer_panel")</code></pre>

<h3>Template Context</h3>
<p>The view provides the following context to the template:</p>
<pre><code class="python">context = {
    "allocations": allocations,    # Allocations for the department
    "lecturers": lecturers,        # All lecturers for dropdown
    "detected_dept": dept,         # User's department
}</code></pre>

<h3>Template Implementation</h3>
<h4>Key Template Components</h4>
<ul>
<li><strong>Allocations table</strong>: Display all course allocations</li>
<li><strong>Edit forms</strong>: Inline forms for each allocation</li>
<li><strong>Lecturer dropdown</strong>: Select from existing lecturers</li>
<li><strong>New lecturer option</strong>: Link to create new lecturer</li>
</ul>

<h4>Sample Template Structure</h4>
<pre><code class="django"><!-- lecture_allocator_panel.html -->
{% extends "base.html" %}

{% block content %}
&lt;h1&gt;Lecture Allocator Panel&lt;/h1&gt;
&lt;p&gt;Department: {{ detected_dept.name }}&lt;/p&gt;

&lt;table class="table"&gt;
    &lt;thead&gt;
        &lt;tr&gt;
            &lt;th&gt;Course Code&lt;/th&gt;
            &lt;th&gt;Course Name&lt;/th&gt;
            &lt;th&gt;Lecturer&lt;/th&gt;
            &lt;th&gt;Actions&lt;/th&gt;
        &lt;/tr&gt;
    &lt;/thead&gt;
    &lt;tbody&gt;
        {% for alloc in allocations %}
        &lt;tr&gt;
            &lt;form method="post"&gt;
                {% csrf_token %}
                &lt;input type="hidden" name="id" value="{{ alloc.id }}"&gt;
                
                &lt;td&gt;
                    &lt;input type="text" name="course_code" 
                           value="{{ alloc.course_code }}" class="form-control"&gt;
                &lt;/td&gt;
                
                &lt;td&gt;
                    &lt;input type="text" name="course_name" 
                           value="{{ alloc.course_name }}" class="form-control"&gt;
                &lt;/td&gt;
                
                &lt;td&gt;
                    &lt;select name="lecturer_id" class="form-control"&gt;
                        &lt;option value=""&gt;-- Select Lecturer --&lt;/option&gt;
                        {% for lecturer in lecturers %}
                        &lt;option value="{{ lecturer.id }}" 
                                {% if alloc.lecturer.id == lecturer.id %}selected{% endif %}&gt;
                            {{ lecturer.display_name }}
                        &lt;/option&gt;
                        {% endfor %}
                        &lt;option value="__new__"&gt;➕ Add New Lecturer&lt;/option&gt;
                    &lt;/select&gt;
                &lt;/td&gt;
                
                &lt;td&gt;
                    &lt;button type="submit" class="btn btn-primary"&gt;Update&lt;/button&gt;
                &lt;/td&gt;
            &lt;/form&gt;
        &lt;/tr&gt;
        {% endfor %}
    &lt;/tbody&gt;
&lt;/table&gt;
{% endblock %}</code></pre>

<h3>Error Handling</h3>
<h4>Common Error Scenarios</h4>
<ul>
<li><strong>No department detected</strong>: Redirect to COD panel with error message</li>
<li><strong>Allocation not found</strong>: 404 error for invalid allocation ID</li>
<li><strong>Unauthorized access</strong>: Cannot edit other departments' allocations</li>
</ul>

<h4>Error Messages</h4>
<pre><code class="python">messages.error(request, "Your department could not be detected. Contact Admin.")</code></pre>

<h3>Use Cases</h3>
<h4>Mid-Semester Adjustments</h4>
<ol>
<li>Change lecturer assignments for courses</li>
<li>Correct course codes or names</li>
<li>Reassign courses due to staff changes</li>
</ol>

<h4>New Lecturer Onboarding</h4>
<ol>
<li>Add new lecturer profiles</li>
<li>Assign courses to new lecturers</li>
<li>Distribute workload evenly</li>
</ol>

<h4>Course Corrections</h4>
<ol>
<li>Fix incorrect course information</li>
<li>Update course names after curriculum changes</li>
<li>Correct allocation errors</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Verify department ownership</strong>: Always check allocation belongs to user's department</li>
<li><strong>Use select_related</strong>: Efficiently load related objects</li>
<li><strong>Clear messaging</strong>: Inform users of successful updates</li>
<li><strong>Redirect appropriately</strong>: Guide users to create new lecturers</li>
<li><strong>Validate inputs</strong>: Ensure course codes are valid</li>
<li><strong>Regular reviews</strong>: Periodically review all allocations</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>Lecturer Panel</strong>: Redirects to create new lecturers</li>
<li><strong>COD Panel</strong>: Shares department detection logic</li>
<li><strong>Course Allocation System</strong>: Updates affect overall allocation data</li>
<li><strong>Timetable Generation</strong>: Changes affect scheduled timetables</li>
</ul>
"""

page2 = DocumentationPage.objects.create(
    title='Lecture Allocator Panel - Course Allocation Editing',
    slug='lecture-allocator-panel-course-allocation-editing',
    short_description='Guide to editing course allocations and lecturer assignments',
    content=page2_content,
    category=categories['lecture-allocator-panel'],
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
print(f"   Created: {page2.title}")

# Page 3: DVC Panel
page3_content = """
<h2>DVC Panel - Course Allocation Approval Workflow</h2>
<p>The DVC (Deputy Vice Chancellor) Panel manages faculty creation and course allocation approval workflows.</p>

<h3>Overview</h3>
<p>The DVC Panel provides two main functions:</p>
<ol>
<li><strong>Faculty Management</strong>: Create, edit, and delete faculties with automated dean account creation</li>
<li><strong>Course Allocation Approval</strong>: Approve or reject course allocations at various levels</li>
</ol>

<h3>Access Control</h3>
<h4>Required Permissions</h4>
<ul>
<li><strong>User Groups</strong>: "DVC" or "DVC Admins"</li>
<li><strong>System-wide access</strong>: Can manage all faculties and allocations</li>
</ul>

<h4>Permission Decorators</h4>
<pre><code class="python">@login_required
@group_required("DVC", "DVC Admins")
def dvc_panel(request):
    # Implementation
    pass</code></pre>

<h3>Faculty Management</h3>
<h4>Automated Dean Creation</h4>
<p>When creating a faculty, the system automatically creates dean and dean admin accounts:</p>
<pre><code class="python">def create_dean_and_admin(faculty: Faculty):
    \"\"\"
    Create dean_<faculty> and dean_<faculty>_admin users automatically.
    \"\"\"
    base_name = normalize_name(faculty.name)
    dean_username = f"dean_{base_name}"
    dean_admin_username = f"{dean_username}_admin"
    
    # Create dean user
    dean_user, created = User.objects.get_or_create(username=dean_username)
    if created or not dean_user.has_usable_password():
        dean_user.set_password(generate_default_password(dean_username))
        dean_user.save()
    
    # Assign to Dean group
    dean_group, _ = Group.objects.get_or_create(name="Dean")
    if dean_group not in dean_user.groups.all():
        dean_user.groups.add(dean_group)
    
    faculty.leader = dean_user
    faculty.save(update_fields=["leader"])
    
    # Create dean admin user (similar process)
    # ...
    
    return dean_user, admin_user</code></pre>

<h4>Faculty CRUD Operations</h4>
<p>The DVC panel supports full CRUD operations for faculties:</p>
<ul>
<li><strong>Create</strong>: Create new faculty with auto-generated dean accounts</li>
<li><strong>Read</strong>: View all faculties with their leaders</li>
<li><strong>Update</strong>: Edit faculty details</li>
<li><strong>Delete</strong>: Remove faculty (cascading effects on departments)</li>
</ul>

<h3>Course Allocation Approval</h3>
<h4>Approval Levels</h4>
<p>The DVC panel supports three levels of approval:</p>
<ol>
<li><strong>Single Allocation</strong>: Approve/reject individual courses</li>
<li><strong>Department-wide</strong>: Approve/reject all allocations for a department</li>
<li><strong>Global</strong>: Approve/reject all allocations system-wide</li>
</ol>

<h4>Allocation Status Logic</h4>
<pre><code class="python">def get_allocation_status(allocation):
    if getattr(allocation, "approved_by_dvc", False):
        return "Approved ✅"
    if getattr(allocation, "rejected_by_dvc", False):
        return "Rejected ❌"
    return "Pending ⏳"</code></pre>

<h3>Submission Control Integration</h3>
<p>The DVC panel only shows allocations from departments that allow DVC submission:</p>
<pre><code class="python">from TT_APP.models import SubmissionControl
allowed_depts = SubmissionControl.objects.filter(
    allow_submission_to_dvc=True,
    department__isnull=False   # ignore legacy/global rows
).values_list("department_id", flat=True)

allocations = (
    CourseAllocation.objects
    .filter(department_id__in=allowed_depts)
    .select_related("department", "lecturer")
    .order_by("department__name", "course_code")
)</code></pre>

<h3>AJAX Endpoints</h3>
<h4>Faculty Operations</h4>
<ul>
<li><strong>create_faculty/add_faculty</strong>: Create new faculty</li>
<li><strong>edit_faculty</strong>: Update existing faculty</li>
<li><strong>delete_faculty</strong>: Remove faculty</li>
</ul>

<h4>Allocation Approval Operations</h4>
<ul>
<li><strong>approve_allocation/reject_allocation</strong>: Single allocation</li>
<li><strong>approve_all_allocations/reject_all_allocations</strong>: Global approval</li>
<li><strong>approve_department_allocations/reject_department_allocations</strong>: Department-wide</li>
</ul>

<h3>Department Resolution Helper</h3>
<p>Helper function to resolve department identifiers:</p>
<pre><code class="python">def _resolve_department(identifier):
    if not identifier:
        return None
    try:
        dept_id = int(identifier)
        return Department.objects.get(pk=dept_id)
    except (ValueError, TypeError, Department.DoesNotExist):
        pass
    try:
        return Department.objects.get(name__iexact=identifier)
    except Department.DoesNotExist:
        pass
    try:
        return Department.objects.get(code__iexact=identifier)
    except Exception:
        pass
    return None</code></pre>

<h3>Disapproved Allocations Endpoint</h3>
<p>Separate endpoint for managing disapproved allocations:</p>
<pre><code class="python">@login_required
def ajax_disapproved_allocations(request):
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action")
        alloc_id = request.POST.get("id")
        
        if action == "update_disapproval_reason":
            # Update reason for disapproval
            pass
        elif action == "approve_allocation":
            # Approve previously disapproved allocation
            pass
        elif action == "approve_all_disapproved":
            # Approve all disapproved allocations
            pass</code></pre>

<h3>Template Context</h3>
<p>The DVC panel provides:</p>
<pre><code class="python">return render(request, "dvc_panel.html", {
    "faculties": faculties,        # All faculties with leaders
    "allocations": allocations,    # Allocations from submission-enabled departments
})</code></pre>

<h3>Security Considerations</h3>
<h4>Access Control</h4>
<ul>
<li><strong>Strict group requirements</strong>: Only DVC and DVC Admins can access</li>
<li><strong>No faculty scope limitation</strong>: Can manage all faculties</li>
<li><strong>Submission control respect</strong>: Only shows allocations from departments allowing submission</li>
</ul>

<h4>Data Integrity</h4>
<ul>
<li><strong>Form validation</strong>: Use FacultyForm for validation</li>
<li><strong>Error handling</strong>: Comprehensive error responses</li>
<li><strong>Atomic operations</strong>: Faculty creation includes user creation</li>
</ul>

<h3>Use Cases</h3>
<h4>New Faculty Establishment</h4>
<ol>
<li>Create new faculty structure</li>
<li>Automatically generate dean accounts</li>
<li>Set up faculty leadership</li>
</ol>

<h4>End-of-Semester Approval</h4>
<ol>
<li>Review all submitted course allocations</li>
<li>Approve compliant allocations</li>
<li>Reject allocations needing corrections</li>
<li>Provide feedback for rejected courses</li>
</ol>

<h4>Bulk Operations</h4>
<ol>
<li>Approve all allocations for a department</li>
<li>Reject all incomplete submissions</li>
<li>Update disapproval reasons in bulk</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Review before approval</strong>: Thoroughly review allocations before approval</li>
<li><strong>Clear rejection reasons</strong>: Provide specific feedback for rejections</li>
<li><strong>Secure account generation</strong>: Handle generated credentials securely</li>
<li><strong>Regular faculty reviews</strong>: Periodically review faculty structure</li>
<li><strong>Backup before deletion</strong>: Archive before deleting faculties</li>
<li><strong>Document approval decisions</strong>: Keep records of approval/rejection decisions</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>Dean Panel</strong>: Created dean accounts can access dean panel</li>
<li><strong>COD Panel</strong>: Approval status affects COD operations</li>
<li><strong>Timetable Department</strong>: Approved allocations can be submitted to timetable</li>
<li><strong>Reporting System</strong>: Approval status included in reports</li>
</ul>
"""

page3 = DocumentationPage.objects.create(
    title='DVC Panel - Course Allocation Approval Workflow',
    slug='dvc-panel-course-allocation-approval-workflow',
    short_description='Guide to faculty management and course allocation approval',
    content=page3_content,
    category=categories['dvc-panel-approval-workflow'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=25,
    version='1.0'
)
print(f"   Created: {page3.title}")

# Page 4: User Account Management
page4_content = """
<h2>Automated User Account Management</h2>
<p>Patterns and utilities for automated user creation and role assignment in the timetabling system.</p>

<h3>Overview</h3>
<p>The system uses automated user creation for:</p>
<ol>
<li><strong>Dean and Dean Admin accounts</strong>: Created when faculties are established</li>
<li><strong>COD and COD Admin accounts</strong>: Created when departments are created</li>
<li><strong>Role-based access control</strong>: Automatic group assignment</li>
<li><strong>Credential generation</strong>: Standardized password generation</li>
</ol>

<h3>Core Helper Functions</h3>
<h4>Name Normalization</h4>
<pre><code class="python">def normalize_name(name: str) -> str:
    \"\"\"Convert to safe username format: spaces -> _, lowercase.\"\"\"
    return name.strip().replace(" ", "_").lower()</code></pre>

<h4>Password Generation</h4>
<pre><code class="python">def generate_default_password(username: str) -> str:
    \"\"\"Default password format.\"\"\"
    return f"{username}@2025"</code></pre>

<h3>Dean Account Creation Pattern</h3>
<h4>Function Signature</h4>
<pre><code class="python">def create_dean_and_admin(faculty: Faculty):
    \"\"\"
    Create dean_<faculty> and dean_<faculty>_admin users automatically.
    Assign groups based on seeding (Dean, Dean Admins).
    \"\"\"</code></pre>

<h4>Implementation Details</h4>
<pre><code class="python">def create_dean_and_admin(faculty: Faculty):
    base_name = normalize_name(faculty.name)
    dean_username = f"dean_{base_name}"
    dean_admin_username = f"{dean_username}_admin"
    
    # --- Dean user ---
    dean_user, created = User.objects.get_or_create(username=dean_username)
    if created or not dean_user.has_usable_password():
        dean_user.set_password(generate_default_password(dean_username))
        dean_user.save()
    
    dean_group, _ = Group.objects.get_or_create(name="Dean")
    if dean_group not in dean_user.groups.all():
        dean_user.groups.add(dean_group)
    dean_user.save()
    
    faculty.leader = dean_user
    faculty.save(update_fields=["leader"])
    
    # --- Dean Admin user ---
    admin_user, created = User.objects.get_or_create(username=dean_admin_username)
    if created or not admin_user.has_usable_password():
        admin_user.set_password(generate_default_password(dean_admin_username))
        admin_user.save()
    
    admin_group, _ = Group.objects.get_or_create(name="Dean Admins")
    if admin_group not in admin_user.groups.all():
        admin_user.groups.add(admin_group)
    admin_user.save()
    
    return dean_user, admin_user</code></pre>

<h3>COD Account Creation Pattern</h3>
<h4>Function Signature</h4>
<pre><code class="python">def create_cod_and_admin(department_name: str):
    \"\"\"
    Create COD and COD Admin accounts for a department.
    Only set password when user is newly created.
    \"\"\"</code></pre>

<h4>Implementation Details</h4>
<pre><code class="python">def create_cod_and_admin(department_name: str):
    base_name = normalize_name(department_name)
    
    # COD account
    cod_username = f"cod_{base_name}"
    cod_password = f"{cod_username}@2025"
    cod_user, created = User.objects.get_or_create(username=cod_username)
    
    if created:
        cod_user.set_password(cod_password)
        cod_user.first_name = "COD"
        cod_user.last_name = department_name
        cod_user.is_active = True
        cod_user.save()
        OrgRole.objects.create(title="COD", user=cod_user)
    else:
        cod_password = None  # don't reset password if user already exists
    
    cod_group, _ = Group.objects.get_or_create(name="COD")
    if cod_group not in cod_user.groups.all():
        cod_user.groups.add(cod_group)
    
    # COD Admin account
    admin_username = f"{cod_username}_admin"
    admin_password = f"{admin_username}@2025"
    admin_user, created_admin = User.objects.get_or_create(username=admin_username)
    
    if created_admin:
        admin_user.set_password(admin_password)
        admin_user.first_name = "COD Admin"
        admin_user.last_name = department_name
        admin_user.is_active = True
        admin_user.save()
        OrgRole.objects.create(title="COD Admin", user=admin_user)
    else:
        admin_password = None  # don't reset password if already exists
    
    cod_admin_group, _ = Group.objects.get_or_create(name="COD Admins")
    if cod_admin_group not in admin_user.groups.all():
        admin_user.groups.add(cod_admin_group)
    
    return cod_user, admin_user, cod_password, admin_password</code></pre>

<h3>Password Management Strategy</h3>
<h4>Password Reset Policy</h4>
<ul>
<li><strong>New users</strong>: Set password using generated default</li>
<li><strong>Existing users</strong>: Don't reset password (password = None)</li>
<li><strong>Usable password check</strong>: Only set if user doesn't have usable password</li>
</ul>

<h4>Implementation Pattern</h4>
<pre><code class="python">user, created = User.objects.get_or_create(username=username)
if created or not user.has_usable_password():
    user.set_password(password)
    user.save()</code></pre>

<h3>Group Assignment Pattern</h3>
<h4>Safe Group Assignment</h4>
<pre><code class="python">group, _ = Group.objects.get_or_create(name=group_name)
if group not in user.groups.all():
    user.groups.add(group)
user.save()</code></pre>

<h3>OrgRole Integration</h3>
<h4>Creating Organizational Roles</h4>
<pre><code class="python">OrgRole.objects.create(title="COD", user=cod_user)
OrgRole.objects.create(title="COD Admin", user=admin_user)</code></pre>

<h3>Username Generation Patterns</h3>
<h4>Faculty-based Usernames</h4>
<ul>
<li><strong>Dean</strong>: dean_{faculty_name}</li>
<li><strong>Dean Admin</strong>: dean_{faculty_name}_admin</li>
</ul>

<h4>Department-based Usernames</h4>
<ul>
<li><strong>COD</strong>: cod_{department_name}</li>
<li><strong>COD Admin</strong>: cod_{department_name}_admin</li>
</ul>

<h3>Error Handling in User Creation</h3>
<h4>Transaction Safety</h4>
<pre><code class="python">@transaction.atomic
def create_entity_with_users(...):
    # User creation is atomic with entity creation
    pass</code></pre>

<h4>Duplicate User Handling</h4>
<pre><code class="python">user, created = User.objects.get_or_create(username=username)
# created indicates if user was newly created
# If not created, user already exists</code></pre>

<h3>Credential Return Pattern</h3>
<h4>Returning Generated Credentials</h4>
<pre><code class="python">return JsonResponse({
    "status": "success",
    # ... other data ...
    "credentials": {
        "cod_username": cod_user.username if cod_user else None,
        "cod_password": cod_pass,  # None if user existed
        "cod_admin_username": admin_user.username if admin_user else None,
        "cod_admin_password": admin_pass,  # None if user existed
    }
})</code></pre>

<h3>Security Considerations</h3>
<h4>Password Security</h4>
<ul>
<li><strong>Default passwords</strong>: Should be changed on first login</li>
<li><strong>Password transmission</strong>: Only return in secure contexts</li>
<li><strong>Password storage</strong>: Django's secure password hashing</li>
</ul>

<h4>Account Security</h4>
<ul>
<li><strong>Account activation</strong>: Set is_active = True for new accounts</li>
<li><strong>Name fields</strong>: Set first_name and last_name appropriately</li>
<li><strong>Group membership</strong>: Ensure correct group assignment</li>
</ul>

<h3>Use Cases</h3>
<h4>New Faculty Setup</h4>
<ol>
<li>Create faculty entity</li>
<li>Automatically create dean accounts</li>
<li>Assign faculty leadership</li>
<li>Provide credentials to new deans</li>
</ol>

<h4>New Department Setup</h4>
<ol>
<li>Create department entity</li>
<li>Automatically create COD accounts</li>
<li>Assign department leadership</li>
<li>Provide credentials to new COD team</li>
</ol>

<h4>Account Recovery</h4>
<ol>
<li>Check if user exists</li>
<li>Reset password if needed</li>
<li>Reassign groups if missing</li>
<li>Update OrgRole if missing</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Use get_or_create</strong>: Avoid duplicate user creation</li>
<li><strong>Check usable_password</strong>: Don't reset working passwords</li>
<li><strong>Secure credential handling</strong>: Only display passwords when absolutely necessary</li>
<li><strong>Group existence check</strong>: Use get_or_create for groups</li>
<li><strong>Transaction safety</strong>: Wrap user creation in transactions</li>
<li><strong>Comprehensive user setup</strong>: Set all relevant user fields</li>
<li><strong>Clear naming conventions</strong>: Consistent username patterns</li>
<li><strong>Document credential policies</strong>: Document password rules and reset procedures</li>
</ol>

<h3>Integration Points</h3>
<ul>
<li><strong>Authentication system</strong>: Created users can log in immediately</li>
<li><strong>Permission system</strong>: Group assignment grants permissions</li>
<li><strong>OrgRole system</strong>: Organizational role tracking</li>
<li><strong>Email system</strong>: Could integrate with email notifications</li>
<li><strong>Audit logging</strong>: Track user creation events</li>
</ul>
"""

page4 = DocumentationPage.objects.create(
    title='Automated User Account Management',
    slug='automated-user-account-management',
    short_description='Patterns for automated user creation and role assignment',
    content=page4_content,
    category=categories['user-account-management'],
    page_type='reference',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=18,
    version='1.0'
)
print(f"   Created: {page4.title}")

# ============================================================
# 4. CREATE SECTIONS WITH CODE EXAMPLES
# ============================================================

print("\n4. CREATING SECTIONS WITH DETAILED CODE EXAMPLES...")

# Section 1: Dean Panel Complete Implementation
section1 = DocumentationSection.objects.create(
    page=page1,
    title='Complete Dean Panel Implementation',
    content='''
<h3>Full Dean Panel View Code</h3>
<p>Complete implementation of the dean panel with all AJAX endpoints.</p>
''',
    order=1,
    is_active=True,
    slug='dean-panel-complete-implementation'
)
print(f"   Created section: {section1.title}")

# Code Example 1: Dean Panel Complete
code1 = CodeExample.objects.create(
    section=section1,
    title='Dean Panel - Complete View Function',
    code='''from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User, Group
from django.db import transaction, IntegrityError

from TT_APP.models import Faculty, Department, OrgRole
from TT_APP.views import group_required


# --- helpers ---
def normalize_name(name: str) -> str:
    \"\"\"Convert to safe username format: spaces -> _, lowercase.\"\"\"
    return name.strip().replace(" ", "_").lower()


def create_cod_and_admin(department_name: str):
    \"\"\"
    Create COD and COD Admin accounts for a department.
    Only set password when user is newly created.
    \"\"\"
    base_name = normalize_name(department_name)

    # COD account
    cod_username = f"cod_{base_name}"
    cod_password = f"{cod_username}@2025"
    cod_user, created = User.objects.get_or_create(username=cod_username)

    if created:
        cod_user.set_password(cod_password)
        cod_user.first_name = "COD"
        cod_user.last_name = department_name
        cod_user.is_active = True
        cod_user.save()
        OrgRole.objects.create(title="COD", user=cod_user)
    else:
        cod_password = None  # don't reset password if user already exists

    cod_group, _ = Group.objects.get_or_create(name="COD")
    if cod_group not in cod_user.groups.all():
        cod_user.groups.add(cod_group)

    # COD Admin account
    admin_username = f"{cod_username}_admin"
    admin_password = f"{admin_username}@2025"
    admin_user, created_admin = User.objects.get_or_create(username=admin_username)

    if created_admin:
        admin_user.set_password(admin_password)
        admin_user.first_name = "COD Admin"
        admin_user.last_name = department_name
        admin_user.is_active = True
        admin_user.save()
        OrgRole.objects.create(title="COD Admin", user=admin_user)
    else:
        admin_password = None  # don't reset password if already exists

    cod_admin_group, _ = Group.objects.get_or_create(name="COD Admins")
    if cod_admin_group not in admin_user.groups.all():
        admin_user.groups.add(cod_admin_group)

    return cod_user, admin_user, cod_password, admin_password


def handle_leader_option(option, department_name):
    \"\"\"Handle leader selection when creating/updating a department.\"\"\"
    if option == "none":
        return None
    try:
        user_id = int(option)
        return User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError, TypeError):
        return None


# --- main view ---
@login_required
@group_required("Dean", "Dean Admins")
@transaction.atomic
def dean_panel(request):
    # Get dean's assigned faculty (if any)
    try:
        dean_faculty = Faculty.objects.get(leader=request.user)
    except Faculty.DoesNotExist:
        dean_faculty = None

    if not dean_faculty:
        return render(request, "dean_panel.html", {
            "faculties": [],
            "departments": [],
            "users": User.objects.none(),
            "error": "You are not assigned as leader of any faculty."
        })

    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action")

        # -------- CREATE --------
        if action == "create":
            try:
                name = request.POST.get("name", "").strip()
                desc = request.POST.get("description", "").strip()
                faculty_id = request.POST.get("faculty_id")
                leader_option = request.POST.get("leader_option")

                if not name or not faculty_id:
                    return JsonResponse({
                        "status": "error",
                        "message": "Name and Faculty are required.",
                        "popup": {"type": "error", "message": "Name and Faculty are required."}
                    }, status=400)

                # Security check: only allow dean's faculty
                faculty = get_object_or_404(Faculty, id=faculty_id)
                if faculty != dean_faculty:
                    return JsonResponse({
                        "status": "error",
                        "message": "You can only manage your own faculty.",
                        "popup": {"type": "error", "message": "You can only manage your own faculty."}
                    }, status=403)

                cod_user = admin_user = cod_pass = admin_pass = None

                if leader_option == "new":
                    cod_user, admin_user, cod_pass, admin_pass = create_cod_and_admin(name)
                    leader = cod_user
                else:
                    leader = handle_leader_option(leader_option, name)

                dept = Department.objects.create(
                    name=name,
                    description=desc,
                    faculty=faculty,
                    leader=leader
                )

                return JsonResponse({
                    "status": "success",
                    "message": f"Department '{dept.name}' created successfully.",
                    "popup": {"type": "success", "message": f"Department '{dept.name}' created successfully."},
                    "department": {
                        "id": dept.id,
                        "name": dept.name,
                        "description": dept.description,
                        "faculty": dept.faculty.name if dept.faculty else None,
                        "leader": dept.leader.username if dept.leader else None
                    },
                    "credentials": {
                        "cod_username": cod_user.username if cod_user else None,
                        "cod_password": cod_pass,
                        "cod_admin_username": admin_user.username if admin_user else None,
                        "cod_admin_password": admin_pass,
                    }
                })
            except IntegrityError as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Database error: {e}",
                    "popup": {"type": "error", "message": f"Database error: {e}"}
                }, status=400)
            except Exception as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Unexpected error: {e}",
                    "popup": {"type": "error", "message": f"Unexpected error: {e}"}
                }, status=500)

        # -------- EDIT --------
        elif action == "edit":
            try:
                dept_id = request.POST.get("id")
                dept = get_object_or_404(Department, id=dept_id, faculty=dean_faculty)

                name = request.POST.get("name", "").strip()
                if not name:
                    return JsonResponse({
                        "status": "error",
                        "message": "Department name cannot be empty.",
                        "popup": {"type": "error", "message": "Department name cannot be empty."}
                    }, status=400)

                dept.name = name
                dept.description = request.POST.get("description", "").strip()
                faculty_id = request.POST.get("faculty_id")

                faculty = get_object_or_404(Faculty, id=faculty_id)
                if faculty != dean_faculty:
                    return JsonResponse({
                        "status": "error",
                        "message": "You can only assign to your own faculty.",
                        "popup": {"type": "error", "message": "You can only assign to your own faculty."}
                    }, status=403)

                dept.faculty = faculty

                leader_option = request.POST.get("leader_option")
                if leader_option == "new":
                    cod_user, _, _, _ = create_cod_and_admin(dept.name)
                    dept.leader = cod_user
                else:
                    dept.leader = handle_leader_option(leader_option, dept.name)

                dept.save()

                return JsonResponse({
                    "status": "success",
                    "message": f"Department '{dept.name}' updated successfully.",
                    "popup": {"type": "success", "message": f"Department '{dept.name}' updated successfully."},
                    "department": {
                        "id": dept.id,
                        "name": dept.name,
                        "description": dept.description,
                        "faculty": dept.faculty.name if dept.faculty else None,
                        "leader": dept.leader.username if dept.leader else None
                    }
                })
            except IntegrityError as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Database error: {e}",
                    "popup": {"type": "error", "message": f"Database error: {e}"}
                }, status=400)
            except Exception as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Unexpected error: {e}",
                    "popup": {"type": "error", "message": f"Unexpected error: {e}"}
                }, status=500)

        # -------- DELETE --------
        elif action == "delete":
            try:
                dept_id = request.POST.get("id")
                dept = get_object_or_404(Department, id=dept_id, faculty=dean_faculty)
                dept_name = dept.name
                dept.delete()
                return JsonResponse({
                    "status": "success",
                    "message": f"Department '{dept_name}' deleted successfully.",
                    "popup": {"type": "success", "message": f"Department '{dept_name}' deleted successfully."},
                    "id": dept_id
                })
            except Exception as e:
                return JsonResponse({
                    "status": "error",
                    "message": f"Delete failed: {e}",
                    "popup": {"type": "error", "message": f"Delete failed: {e}"}
                }, status=500)

        return JsonResponse({
            "status": "error",
            "message": "Invalid action.",
            "popup": {"type": "error", "message": "Invalid action."}
        }, status=400)

    # -------- GET request → render page --------
    faculties = [dean_faculty]
    departments = Department.objects.filter(faculty=dean_faculty).select_related("faculty", "leader")
    users = User.objects.all()

    return render(request, "dean_panel.html", {
        "faculties": faculties,
        "departments": departments,
        "users": users
    })''',
    language='python',
    order=1,
    description='Complete dean panel implementation with all helper functions'
)

# Section 2: DVC Panel AJAX Endpoints
section2 = DocumentationSection.objects.create(
    page=page3,
    title='DVC Panel AJAX Endpoints',
    content='''
<h3>Complete DVC Panel AJAX Implementation</h3>
<p>All AJAX endpoints for faculty management and allocation approval.</p>
''',
    order=1,
    is_active=True,
    slug='dvc-panel-ajax-endpoints'
)
print(f"   Created section: {section2.title}")

# Code Example 2: DVC Panel AJAX
code2 = CodeExample.objects.create(
    section=section2,
    title='DVC Panel - AJAX Endpoints',
    code='''# ------------------- DVC PANEL -------------------
@login_required
@group_required("DVC", "DVC Admins")
def dvc_panel(request):
    \"\"\"
    DVC Panel:
    - Faculty CRUD (auto-creates dean & dean admin users)
    - Approve/Reject Allocations (single, dept-wide, global)
    \"\"\"
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = (request.POST.get("action") or "").strip()

        # ---------------- FACULTY CREATE ----------------
        if action in ("create_faculty", "add_faculty"):
            name = request.POST.get("name", "").strip()
            desc = request.POST.get("description", "").strip()

            if not name:
                return JsonResponse({"status": "error", "message": "Name is required"}, status=400)

            form = FacultyForm({"name": name})
            if not form.is_valid():
                return JsonResponse({"status": "error", "message": form.errors.get_json_data()}, status=400)

            try:
                faculty = Faculty.objects.create(name=name, description=desc or None)
            except IntegrityError:
                return JsonResponse({"status": "error", "message": "Faculty already exists"}, status=400)

            # Auto-create dean and dean admin
            create_dean_and_admin(faculty)

            # Add requester to DVC Admins group
            dvc_group, _ = Group.objects.get_or_create(name="DVC Admins")
            if request.user and request.user.is_authenticated:
                request.user.groups.add(dvc_group)

            return JsonResponse({
                "status": "success",
                "message": "Faculty created ✅",
                "faculty": {
                    "id": faculty.id,
                    "name": faculty.name,
                    "description": faculty.description,
                    "leader": faculty.leader.username if faculty.leader else None,
                }
            })

        # ---------------- FACULTY EDIT ----------------
        if action == "edit_faculty":
            pk = request.POST.get("id")
            faculty = get_object_or_404(Faculty, pk=pk)
            name = request.POST.get("name", "").strip()
            desc = request.POST.get("description", "").strip()

            form = FacultyForm({"name": name}, instance=faculty)
            if not form.is_valid():
                return JsonResponse({"status": "error", "message": form.errors.get_json_data()}, status=400)

            faculty.name = name or faculty.name
            faculty.description = desc if desc != "" else faculty.description
            faculty.save(update_fields=["name", "description"])

            # Ensure dean + dean admin exist (update if renamed)
            create_dean_and_admin(faculty)

            return JsonResponse({
                "status": "success",
                "message": "Faculty updated ✅",
                "faculty": {
                    "id": faculty.id,
                    "name": faculty.name,
                    "description": faculty.description,
                    "leader": faculty.leader.username if faculty.leader else None,
                }
            })

        # ---------------- FACULTY DELETE ----------------
        if action == "delete_faculty":
            pk = request.POST.get("id")
            faculty = get_object_or_404(Faculty, pk=pk)
            faculty.delete()
            return JsonResponse({"status": "success", "message": "Faculty deleted ❌", "id": pk})

        # ---------------- SINGLE ALLOCATION ----------------
        if action in ["approve_allocation", "reject_allocation"]:
            pk = request.POST.get("id")
            allocation = get_object_or_404(CourseAllocation, pk=pk)
            allocation.approved_by_dvc = action == "approve_allocation"
            if hasattr(allocation, "rejected_by_dvc"):
                allocation.rejected_by_dvc = action == "reject_allocation"
            allocation.save()
            return JsonResponse({
                "status": "success",
                "message": f"{allocation.course_code} {'approved ✅' if action == 'approve_allocation' else 'rejected ❌'}",
                "id": allocation.id,
                "status_display": get_allocation_status(allocation),
            })

        # ---------------- GLOBAL ALLOCATIONS ----------------
        if action in ["approve_all_allocations", "reject_all_allocations"]:
            if "rejected_by_dvc" in [f.name for f in CourseAllocation._meta.fields]:
                updated = CourseAllocation.objects.all().update(
                    approved_by_dvc=action == "approve_all_allocations",
                    rejected_by_dvc=action == "reject_all_allocations"
                )
            else:
                updated = CourseAllocation.objects.all().update(
                    approved_by_dvc=action == "approve_all_allocations"
                )
            return JsonResponse({
                "status": "success",
                "message": f"{'Approved' if action == 'approve_all_allocations' else 'Rejected'} {updated} allocations",
                "updated": updated
            })

        # ---------------- DEPARTMENT-WISE ALLOCATIONS ----------------
        if action in ["approve_department_allocations", "reject_department_allocations"]:
            dept_param = request.POST.get("department_id") or request.POST.get("department")
            dept = _resolve_department(dept_param)
            if not dept:
                return JsonResponse({"status": "error", "message": "Department not found"}, status=404)

            if "rejected_by_dvc" in [f.name for f in CourseAllocation._meta.fields]:
                updated = CourseAllocation.objects.filter(department=dept).update(
                    approved_by_dvc=action == "approve_department_allocations",
                    rejected_by_dvc=action == "reject_department_allocations"
                )
            else:
                updated = CourseAllocation.objects.filter(department=dept).update(
                    approved_by_dvc=action == "approve_department_allocations"
                )
            return JsonResponse({
                "status": "success",
                "message": f"{'Approved' if action == 'approve_department_allocations' else 'Rejected'} {updated} allocations for {dept.name}",
                "department_id": dept.id,
                "updated": updated,
            })

        return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)

    faculties = Faculty.objects.select_related("leader").all()

    # ✅ Only include allocations for departments that allow submission to DVC
    from TT_APP.models import SubmissionControl
    allowed_depts = SubmissionControl.objects.filter(
        allow_submission_to_dvc=True,
        department__isnull=False   # ignore legacy/global rows
    ).values_list("department_id", flat=True)

    allocations = (
        CourseAllocation.objects
        .filter(department_id__in=allowed_depts)
        .select_related("department", "lecturer")
        .order_by("department__name", "course_code")
    )

    # Add status labels
    for alloc in allocations:
        alloc.status_display = alloc.status_label()

    return render(request, "dvc_panel.html", {
        "faculties": faculties,
        "allocations": allocations,
    })''',
    language='python',
    order=1,
    description='DVC panel AJAX endpoints for faculty and allocation management'
)

# Section 3: Template Examples
section3 = DocumentationSection.objects.create(
    page=page1,
    title='Template Implementation Examples',
    content='''
<h3>HTML Templates for Dean and DVC Panels</h3>
<p>Example templates for implementing the dean and DVC panel interfaces.</p>
''',
    order=2,
    is_active=True,
    slug='template-implementation-examples'
)
print(f"   Created section: {section3.title}")

# Code Example 3: Template Code
code3 = CodeExample.objects.create(
    section=section3,
    title='Dean Panel Template Example',
    code='''<!-- dean_panel.html -->
{% extends "base.html" %}

{% block content %}
<div class="container">
    <h1>Dean Panel - Faculty & Department Management</h1>
    
    {% if error %}
    <div class="alert alert-danger">
        {{ error }}
    </div>
    {% endif %}
    
    <!-- Faculties Section -->
    <div class="card mb-4">
        <div class="card-header">
            <h2 class="h5 mb-0">Your Faculty</h2>
        </div>
        <div class="card-body">
            {% for faculty in faculties %}
            <div class="alert alert-info">
                <strong>{{ faculty.name }}</strong>
                {% if faculty.description %}
                <p class="mb-0">{{ faculty.description }}</p>
                {% endif %}
                <small>Leader: {{ faculty.leader.username|default:"Not assigned" }}</small>
            </div>
            {% endfor %}
        </div>
    </div>
    
    <!-- Departments Section -->
    <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
            <h2 class="h5 mb-0">Departments in Your Faculty</h2>
            <button class="btn btn-primary btn-sm" data-toggle="modal" data-target="#createDepartmentModal">
                + Add Department
            </button>
        </div>
        <div class="card-body">
            {% if departments %}
            <div class="table-responsive">
                <table class="table table-hover">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Description</th>
                            <th>Faculty</th>
                            <th>Leader (COD)</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for dept in departments %}
                        <tr>
                            <td>{{ dept.name }}</td>
                            <td>{{ dept.description|default:"-" }}</td>
                            <td>{{ dept.faculty.name }}</td>
                            <td>{{ dept.leader.username|default:"Not assigned" }}</td>
                            <td>
                                <button class="btn btn-sm btn-outline-primary edit-department" 
                                        data-id="{{ dept.id }}"
                                        data-name="{{ dept.name }}"
                                        data-description="{{ dept.description|default:"" }}"
                                        data-faculty-id="{{ dept.faculty.id }}"
                                        data-leader-id="{{ dept.leader.id|default:"" }}">
                                    Edit
                                </button>
                                <button class="btn btn-sm btn-outline-danger delete-department" 
                                        data-id="{{ dept.id }}"
                                        data-name="{{ dept.name }}">
                                    Delete
                                </button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% else %}
            <div class="alert alert-warning">
                No departments found in your faculty.
            </div>
            {% endif %}
        </div>
    </div>
</div>

<!-- Create Department Modal -->
<div class="modal fade" id="createDepartmentModal" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">Create New Department</h5>
                <button type="button" class="close" data-dismiss="modal">&times;</button>
            </div>
            <form id="createDepartmentForm">
                <div class="modal-body">
                    {% csrf_token %}
                    <input type="hidden" name="action" value="create">
                    
                    <div class="form-group">
                        <label for="deptName">Department Name *</label>
                        <input type="text" class="form-control" id="deptName" name="name" required>
                    </div>
                    
                    <div class="form-group">
                        <label for="deptDescription">Description</label>
                        <textarea class="form-control" id="deptDescription" name="description" rows="2"></textarea>
                    </div>
                    
                    <div class="form-group">
                        <label for="deptFaculty">Faculty</label>
                        <select class="form-control" id="deptFaculty" name="faculty_id" required>
                            {% for faculty in faculties %}
                            <option value="{{ faculty.id }}">{{ faculty.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    
                    <div class="form-group">
                        <label>Department Leader (COD)</label>
                        <div>
                            <div class="form-check">
                                <input class="form-check-input" type="radio" name="leader_option" 
                                       id="leaderNew" value="new" checked>
                                <label class="form-check-label" for="leaderNew">
                                    Create new COD account
                                </label>
                            </div>
                            <div class="form-check">
                                <input class="form-check-input" type="radio" name="leader_option" 
                                       id="leaderNone" value="none">
                                <label class="form-check-label" for="leaderNone">
                                    No leader (assign later)
                                </label>
                            </div>
                            <div class="form-check">
                                <input class="form-check-input" type="radio" name="leader_option" 
                                       id="leaderExisting" value="existing">
                                <label class="form-check-label" for="leaderExisting">
                                    Assign existing user
                                </label>
                            </div>
                        </div>
                        
                        <div id="existingUserSelect" class="mt-2" style="display: none;">
                            <select class="form-control" name="leader_existing">
                                <option value="">-- Select User --</option>
                                {% for user in users %}
                                <option value="{{ user.id }}">{{ user.username }} ({{ user.get_full_name }})</option>
                                {% endfor %}
                            </select>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Department</button>
                </div>
            </form>
        </div>
    </div>
</div>

<!-- Edit Department Modal -->
<div class="modal fade" id="editDepartmentModal" tabindex="-1">
    <!-- Similar structure to create modal -->
</div>

<!-- JavaScript for AJAX operations -->
<script>
$(document).ready(function() {
    // Toggle existing user select
    $('input[name="leader_option"]').change(function() {
        if ($(this).val() === 'existing') {
            $('#existingUserSelect').show();
        } else {
            $('#existingUserSelect').hide();
        }
    });
    
    // Create department form
    $('#createDepartmentForm').submit(function(e) {
        e.preventDefault();
        
        const formData = $(this).serialize();
        
        $.ajax({
            url: window.location.href,
            type: 'POST',
            data: formData,
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            },
            success: function(response) {
                if (response.status === 'success') {
                    // Show success popup
                    showPopup(response.popup.type, response.popup.message);
                    
                    // Reload page to show new department
                    setTimeout(() => location.reload(), 1500);
                    
                    // Show credentials if generated
                    if (response.credentials && response.credentials.cod_password) {
                        showCredentialsPopup(response.credentials);
                    }
                } else {
                    showPopup('error', response.popup.message);
                }
            },
            error: function(xhr) {
                showPopup('error', 'An error occurred. Please try again.');
            }
        });
    });
    
    // Edit department button
    $('.edit-department').click(function() {
        const deptId = $(this).data('id');
        const deptName = $(this).data('name');
        const deptDesc = $(this).data('description');
        const facultyId = $(this).data('faculty-id');
        const leaderId = $(this).data('leader-id');
        
        // Populate edit modal
        $('#editDeptId').val(deptId);
        $('#editDeptName').val(deptName);
        $('#editDeptDescription').val(deptDesc);
        $('#editDeptFaculty').val(facultyId);
        
        if (leaderId) {
            $('#editLeaderExisting').val(leaderId).prop('checked', true);
            $('#editExistingUserSelect').show();
            $('#editExistingUserSelect select').val(leaderId);
        }
        
        $('#editDepartmentModal').modal('show');
    });
    
    // Delete department button
    $('.delete-department').click(function() {
        const deptId = $(this).data('id');
        const deptName = $(this).data('name');
        
        if (confirm(`Delete department "${deptName}"? This action cannot be undone.`)) {
            $.ajax({
                url: window.location.href,
                type: 'POST',
                data: {
                    action: 'delete',
                    id: deptId,
                    csrfmiddlewaretoken: '{{ csrf_token }}'
                },
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                },
                success: function(response) {
                    if (response.status === 'success') {
                        showPopup('success', response.popup.message);
                        setTimeout(() => location.reload(), 1500);
                    } else {
                        showPopup('error', response.popup.message);
                    }
                }
            });
        }
    });
    
    // Popup utilities
    function showPopup(type, message) {
        const alertClass = type === 'success' ? 'alert-success' : 'alert-danger';
        const popup = $(`
            <div class="alert ${alertClass} alert-dismissible fade show fixed-top m-3" role="alert">
                ${message}
                <button type="button" class="close" data-dismiss="alert">&times;</button>
            </div>
        `);
        $('body').append(popup);
        setTimeout(() => popup.alert('close'), 5000);
    }
    
    function showCredentialsPopup(credentials) {
        let message = '<strong>Generated Accounts:</strong><br>';
        
        if (credentials.cod_username && credentials.cod_password) {
            message += `COD: ${credentials.cod_username} / ${credentials.cod_password}<br>`;
        }
        if (credentials.cod_admin_username && credentials.cod_admin_password) {
            message += `COD Admin: ${credentials.cod_admin_username} / ${credentials.cod_admin_password}<br>`;
        }
        
        message += '<br><em>Please save these credentials securely.</em>';
        
        showPopup('info', message);
    }
});
</script>
{% endblock %}''',
    language='django',
    order=1,
    description='Complete dean panel template with JavaScript'
)

# Section 4: Helper Functions
section4 = DocumentationSection.objects.create(
    page=page4,
    title='Complete Helper Functions',
    content='''
<h3>All Helper Functions for User Management</h3>
<p>Complete collection of helper functions used across the system.</p>
''',
    order=1,
    is_active=True,
    slug='complete-helper-functions'
)
print(f"   Created section: {section4.title}")

# Code Example 4: Helper Functions
code4 = CodeExample.objects.create(
    section=section4,
    title='All User Management Helper Functions',
    code='''# ==================== USER MANAGEMENT HELPERS ====================

def normalize_name(name: str) -> str:
    \"\"\"
    Convert to safe username format: spaces -> _, lowercase.
    
    Examples:
        "Computer Science" -> "computer_science"
        "Electrical Engineering" -> "electrical_engineering"
        "  Business Admin  " -> "business_admin"
    \"\"\"
    return name.strip().replace(" ", "_").lower()


def generate_default_password(username: str) -> str:
    \"\"\"
    Generate default password in standard format.
    
    Format: {username}@2025
    
    Example:
        "dean_computer_science" -> "dean_computer_science@2025"
    \"\"\"
    return f"{username}@2025"


def create_dean_and_admin(faculty: Faculty):
    \"\"\"
    Create dean_<faculty> and dean_<faculty>_admin users automatically.
    Assign groups based on seeding (Dean, Dean Admins).
    
    Args:
        faculty: Faculty instance to create accounts for
        
    Returns:
        tuple: (dean_user, admin_user)
        
    Process:
        1. Generate usernames based on faculty name
        2. Create/get users
        3. Set passwords if new or no usable password
        4. Assign to appropriate groups
        5. Set faculty leader
        6. Return user objects
    \"\"\"
    base_name = normalize_name(faculty.name)
    dean_username = f"dean_{base_name}"
    dean_admin_username = f"{dean_username}_admin"

    # --- Dean user ---
    dean_user, created = User.objects.get_or_create(username=dean_username)
    if created or not dean_user.has_usable_password():
        dean_user.set_password(generate_default_password(dean_username))
        dean_user.save()

    dean_group, _ = Group.objects.get_or_create(name="Dean")
    if dean_group not in dean_user.groups.all():
        dean_user.groups.add(dean_group)
    dean_user.save()

    faculty.leader = dean_user
    faculty.save(update_fields=["leader"])

    # --- Dean Admin user ---
    admin_user, created = User.objects.get_or_create(username=dean_admin_username)
    if created or not admin_user.has_usable_password():
        admin_user.set_password(generate_default_password(dean_admin_username))
        admin_user.save()

    admin_group, _ = Group.objects.get_or_create(name="Dean Admins")
    if admin_group not in admin_user.groups.all():
        admin_user.groups.add(admin_group)
    admin_user.save()

    return dean_user, admin_user


def create_cod_and_admin(department_name: str):
    \"\"\"
    Create COD and COD Admin accounts for a department.
    Only set password when user is newly created.
    
    Args:
        department_name: Name of department to create accounts for
        
    Returns:
        tuple: (cod_user, admin_user, cod_password, admin_password)
        Note: passwords are None if users already existed
        
    Process:
        1. Generate usernames based on department name
        2. Create/get COD user
        3. Set password and details if new
        4. Create OrgRole for COD
        5. Assign to COD group
        6. Repeat for COD Admin
        7. Return users and passwords (if generated)
    \"\"\"
    base_name = normalize_name(department_name)

    # COD account
    cod_username = f"cod_{base_name}"
    cod_password = f"{cod_username}@2025"
    cod_user, created = User.objects.get_or_create(username=cod_username)

    if created:
        cod_user.set_password(cod_password)
        cod_user.first_name = "COD"
        cod_user.last_name = department_name
        cod_user.is_active = True
        cod_user.save()
        OrgRole.objects.create(title="COD", user=cod_user)
    else:
        cod_password = None  # don't reset password if user already exists

    cod_group, _ = Group.objects.get_or_create(name="COD")
    if cod_group not in cod_user.groups.all():
        cod_user.groups.add(cod_group)

    # COD Admin account
    admin_username = f"{cod_username}_admin"
    admin_password = f"{admin_username}@2025"
    admin_user, created_admin = User.objects.get_or_create(username=admin_username)

    if created_admin:
        admin_user.set_password(admin_password)
        admin_user.first_name = "COD Admin"
        admin_user.last_name = department_name
        admin_user.is_active = True
        admin_user.save()
        OrgRole.objects.create(title="COD Admin", user=admin_user)
    else:
        admin_password = None  # don't reset password if already exists

    cod_admin_group, _ = Group.objects.get_or_create(name="COD Admins")
    if cod_admin_group not in admin_user.groups.all():
        admin_user.groups.add(cod_admin_group)

    return cod_user, admin_user, cod_password, admin_password


def handle_leader_option(option, department_name):
    \"\"\"
    Handle leader selection when creating/updating a department.
    
    Args:
        option: Leader selection option ("new", "none", or user ID string)
        department_name: Department name (used for new COD creation)
        
    Returns:
        User or None: Selected user object or None
        
    Options:
        - "new": Create new COD account for department
        - "none": No leader assigned
        - user ID: Assign existing user as leader
    \"\"\"
    if option == "none":
        return None
    try:
        user_id = int(option)
        return User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError, TypeError):
        return None


def get_allocation_status(allocation):
    \"\"\"
    Get human-readable status for a course allocation.
    
    Args:
        allocation: CourseAllocation instance
        
    Returns:
        str: Status with emoji indicator
        
    Statuses:
        - Approved ✅: approved_by_dvc = True
        - Rejected ❌: rejected_by_dvc = True
        - Pending ⏳: Neither approved nor rejected
    \"\"\"
    if getattr(allocation, "approved_by_dvc", False):
        return "Approved ✅"
    if getattr(allocation, "rejected_by_dvc", False):
        return "Rejected ❌"
    return "Pending ⏳"


def _resolve_department(identifier):
    \"\"\"
    Resolve department identifier to Department object.
    
    Args:
        identifier: Department ID (int), name (str), or code (str)
        
    Returns:
        Department or None: Department object if found
        
    Resolution order:
        1. Try as integer ID
        2. Try as exact department name
        3. Try as exact department code
        4. Return None if not found
    \"\"\"
    if not identifier:
        return None
    try:
        dept_id = int(identifier)
        return Department.objects.get(pk=dept_id)
    except (ValueError, TypeError, Department.DoesNotExist):
        pass
    try:
        return Department.objects.get(name__iexact=identifier)
    except Department.DoesNotExist:
        pass
    try:
        return Department.objects.get(code__iexact=identifier)
    except Exception:
        pass
    return None


# ==================== PASSWORD POLICY HELPERS ====================

def is_password_secure(password: str) -> bool:
    \"\"\"
    Check if password meets security requirements.
    
    Requirements:
        - At least 8 characters
        - Contains uppercase letter
        - Contains lowercase letter
        - Contains digit
        - Contains special character
    
    Returns:
        bool: True if password meets all requirements
    \"\"\"
    if len(password) < 8:
        return False
    
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    
    return has_upper and has_lower and has_digit and has_special


def generate_secure_password(length: int = 12) -> str:
    \"\"\"
    Generate a secure random password.
    
    Args:
        length: Password length (default: 12)
        
    Returns:
        str: Secure random password
    \"\"\"
    import random
    import string
    
    # Character sets
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*"
    
    # Ensure at least one of each type
    password = [
        random.choice(lowercase),
        random.choice(uppercase),
        random.choice(digits),
        random.choice(special)
    ]
    
    # Fill rest with random characters
    all_chars = lowercase + uppercase + digits + special
    password.extend(random.choice(all_chars) for _ in range(length - 4))
    
    # Shuffle and join
    random.shuffle(password)
    return ''.join(password)


# ==================== GROUP MANAGEMENT HELPERS ====================

def ensure_user_in_group(user: User, group_name: str) -> bool:
    \"\"\"
    Ensure user is in specified group.
    
    Args:
        user: User object
        group_name: Name of group
        
    Returns:
        bool: True if user was added or already in group
    \"\"\"
    group, created = Group.objects.get_or_create(name=group_name)
    if group not in user.groups.all():
        user.groups.add(group)
        user.save()
        return True
    return False


def remove_user_from_group(user: User, group_name: str) -> bool:
    \"\"\"
    Remove user from specified group.
    
    Args:
        user: User object
        group_name: Name of group
        
    Returns:
        bool: True if user was removed from group
    \"\"\"
    try:
        group = Group.objects.get(name=group_name)
        if group in user.groups.all():
            user.groups.remove(group)
            user.save()
            return True
    except Group.DoesNotExist:
        pass
    return False


def get_users_in_group(group_name: str):
    \"\"\"
    Get all users in specified group.
    
    Args:
        group_name: Name of group
        
    Returns:
        QuerySet: Users in the group
    \"\"\"
    try:
        group = Group.objects.get(name=group_name)
        return group.user_set.all()
    except Group.DoesNotExist:
        return User.objects.none()


# ==================== AUDIT LOGGING ====================

def log_user_creation(user: User, created_by: User, reason: str = ""):
    \"\"\"
    Log user creation event.
    
    Args:
        user: Created user
        created_by: User who created the account
        reason: Reason for creation
    \"\"\"
    from TT_APP.models import AuditLog
    
    AuditLog.objects.create(
        action="USER_CREATE",
        user=created_by,
        target_user=user,
        details={
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "is_active": user.is_active,
            "reason": reason,
            "groups": list(user.groups.values_list('name', flat=True))
        }
    )


def log_group_change(user: User, changed_by: User, action: str, group_name: str):
    \"\"\"
    Log group membership change.
    
    Args:
        user: Affected user
        changed_by: User who made the change
        action: "ADD" or "REMOVE"
        group_name: Group name
    \"\"\"
    from TT_APP.models import AuditLog
    
    AuditLog.objects.create(
        action=f"GROUP_{action}",
        user=changed_by,
        target_user=user,
        details={
            "group": group_name,
            "username": user.username
        }
    )''',
    language='python',
    order=1,
    description='Complete collection of user management helper functions'
)

# ============================================================
# 5. ASSIGN TAGS TO PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO PAGES...")

# Define tag assignments
tag_assignments = {
    page1: ['dean-panel', 'faculty-management', 'department-management', 'user-creation', 'role-management'],
    page2: ['lecture-allocator', 'course-allocation', 'department-management'],
    page3: ['dvc-panel', 'approval-workflow', 'faculty-management', 'course-allocation'],
    page4: ['user-creation', 'role-management', 'authentication', 'dean-panel', 'cod-panel'],
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
    (page1, page4, "User creation patterns used in dean panel"),
    (page1, page2, "Departments created here appear in lecture allocator"),
    (page2, page1, "Department context comes from dean panel"),
    (page3, page1, "Faculties created here appear in dean panel"),
    (page3, page4, "Automated user creation for deans"),
    (page4, page1, "User creation helpers used in dean panel"),
    (page4, page3, "User creation helpers used in DVC panel"),
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
print("DEAN, LECTURE ALLOCATOR, AND DVC PANEL DOCUMENTATION COMPLETE")
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

📚 NEW PANEL DOCUMENTATION:
   1. Dean Panel - Faculty and Department Management
   2. Lecture Allocator Panel - Course Allocation Editing
   3. DVC Panel - Course Allocation Approval Workflow
   4. Automated User Account Management

🔧 KEY FEATURES DOCUMENTED:
   • Faculty management with scope enforcement
   • Department creation with automated COD accounts
   • Course allocation editing for CODs
   • DVC approval workflow at multiple levels
   • Automated user creation patterns
   • Password management strategies
   • Group and role assignment
   • Security and access control

💻 TECHNICAL IMPLEMENTATION:
   • Complete Django view implementations
   • AJAX endpoints with proper error handling
   • Automated user creation with password policies
   • Transaction-safe operations
   • Template examples with JavaScript
   • Helper functions for common patterns
   • Security enforcement patterns
   • Audit logging examples

🔗 INTEGRATION POINTS:
   • Dean panel creates departments for COD panel
   • DVC panel creates faculties for dean panel
   • User creation patterns used across all panels
   • Department detection shared between panels
   • Approval workflow connects COD and DVC panels

🚀 ACCESS POINTS:
   Dean Panel:          /documentation/page/dean-panel-faculty-department-management/
   Lecture Allocator:   /documentation/page/lecture-allocator-panel-course-allocation-editing/
   DVC Panel:           /documentation/page/dvc-panel-course-allocation-approval-workflow/
   User Management:     /documentation/page/automated-user-account-management/

🎯 OPERATIONAL WORKFLOWS:
   1. DVC creates faculties -> automatic dean accounts
   2. Dean creates departments -> automatic COD accounts
   3. COD allocates courses -> edits via lecture allocator
   4. COD submits allocations -> DVC reviews and approves
   5. Approved allocations -> forwarded to timetable department

✅ Documentation successfully created for all major administrative panels!
""")

print("=" * 80)
print("Documentation includes:")
print("• Complete panel implementations with AJAX")
print("• Automated user creation patterns")
print("• Security and access control mechanisms")
print("• Template examples with JavaScript")
print("• Helper functions for common operations")
print("• Integration patterns between panels")
print("• Best practices for administrative operations")
print("=" * 80)