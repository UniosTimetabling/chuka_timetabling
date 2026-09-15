#!/usr/bin/env python
"""
UNIVERSITY TIMETABLING SYSTEM - COMPREHENSIVE DOCUMENTATION SETUP
This script populates the database with detailed documentation about how the system works.
Run: python manage.py shell < this_script.py
OR: python this_script.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'university_timetable_system.settings')
django.setup()

from documentation.models import (
    DocumentationCategory, DocumentationPage, DocumentationSection, 
    CodeExample, DocumentationTag, PageTag, DocumentationImage
)
from django.contrib.auth.models import User
from django.utils import timezone

print("=" * 80)
print("UNIVERSITY TIMETABLING SYSTEM - DOCUMENTATION POPULATION")
print("=" * 80)

# Get or create admin user for documentation
try:
    author = User.objects.filter(is_superuser=True).first()
    if not author:
        author = User.objects.create_superuser(
            username='admin',
            email='admin@university.edu',
            password='admin123'
        )
        print(f"Created admin user: {author.username}")
    else:
        print(f"Using existing admin: {author.username}")
except Exception as e:
    print(f"Error with user: {e}")
    exit()

# ============================================================
# 1. CREATE CATEGORIES
# ============================================================

print("\n1. CREATING DOCUMENTATION CATEGORIES...")

categories_data = [
    {
        'name': 'System Overview',
        'slug': 'system-overview',
        'description': 'Introduction to the University Timetabling System',
        'icon': 'fas fa-info-circle',
        'order': 1,
        'access_level': 'all',
    },
    {
        'name': 'User Roles & Permissions',
        'slug': 'user-roles-permissions',
        'description': 'Detailed guide to user roles, permissions, and responsibilities',
        'icon': 'fas fa-users-cog',
        'order': 2,
        'access_level': 'all',
    },
    {
        'name': 'Course Management',
        'slug': 'course-management',
        'description': 'Managing courses, programs, and course allocations',
        'icon': 'fas fa-book-open',
        'order': 3,
        'access_level': 'department',
    },
    {
        'name': 'Timetable Generation',
        'slug': 'timetable-generation',
        'description': 'Creating and managing class timetables',
        'icon': 'fas fa-calendar-alt',
        'order': 4,
        'access_level': 'management',
    },
    {
        'name': 'Examination Management',
        'slug': 'examination-management',
        'description': 'Exam scheduling and management',
        'icon': 'fas fa-graduation-cap',
        'order': 5,
        'access_level': 'management',
    },
    {
        'name': 'Resource Management',
        'slug': 'resource-management',
        'description': 'Managing venues, buildings, and lab facilities',
        'icon': 'fas fa-building',
        'order': 6,
        'access_level': 'management',
    },
    {
        'name': 'Workflow & Approvals',
        'slug': 'workflow-approvals',
        'description': 'Submission workflows and approval processes',
        'icon': 'fas fa-tasks',
        'order': 7,
        'access_level': 'all',
    },
    {
        'name': 'Technical Reference',
        'slug': 'technical-reference',
        'description': 'Technical documentation for administrators',
        'icon': 'fas fa-code',
        'order': 8,
        'access_level': 'admin',
    },
    {
        'name': 'Administrator Guide',
        'slug': 'administrator-guide',
        'description': 'System administration and configuration',
        'icon': 'fas fa-cogs',
        'order': 9,
        'access_level': 'admin',
    },
    {
        'name': 'FAQ & Troubleshooting',
        'slug': 'faq-troubleshooting',
        'description': 'Frequently asked questions and problem solving',
        'icon': 'fas fa-question-circle',
        'order': 10,
        'access_level': 'all',
    },
]

categories = {}
for cat_data in categories_data:
    category, created = DocumentationCategory.objects.get_or_create(
        slug=cat_data['slug'],
        defaults=cat_data
    )
    categories[cat_data['slug']] = category
    status = "Created" if created else "Exists"
    print(f"   {status}: {category.name}")

# ============================================================
# 2. CREATE TAGS
# ============================================================

print("\n2. CREATING DOCUMENTATION TAGS...")

tags_data = [
    {'name': 'DVC', 'slug': 'dvc', 'color': '#dc3545'},
    {'name': 'Dean', 'slug': 'dean', 'color': '#17a2b8'},
    {'name': 'COD', 'slug': 'cod', 'color': '#28a745'},
    {'name': 'Timetable', 'slug': 'timetable', 'color': '#007bff'},
    {'name': 'Course Allocation', 'slug': 'course-allocation', 'color': '#6f42c1'},
    {'name': 'Examination', 'slug': 'examination', 'color': '#fd7e14'},
    {'name': 'Venue Management', 'slug': 'venue-management', 'color': '#20c997'},
    {'name': 'Workflow', 'slug': 'workflow', 'color': '#e83e8c'},
    {'name': 'Technical', 'slug': 'technical', 'color': '#343a40'},
    {'name': 'Administration', 'slug': 'administration', 'color': '#6610f2'},
    {'name': 'User Guide', 'slug': 'user-guide', 'color': '#20c997'},
    {'name': 'System Architecture', 'slug': 'system-architecture', 'color': '#6c757d'},
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

# Page 1: System Overview
overview_content = """
<h2>University Timetabling System Overview</h2>
<p>The University Timetabling System is a comprehensive web-based platform designed to manage all aspects of academic scheduling, from course allocations to exam timetables. The system supports multiple user roles with specific permissions and workflows.</p>

<h3>System Architecture</h3>
<p>The system is built on Django framework with the following key components:</p>
<ul>
<li><strong>Core Application</strong>: Manages users, roles, permissions, and system-wide settings</li>
<li><strong>TT_APP</strong>: Handles timetable generation, course allocations, and scheduling</li>
<li><strong>Documentation Module</strong>: Internal documentation system (this module)</li>
<li><strong>Audit Logging</strong>: Tracks all system activities for accountability</li>
</ul>

<h3>Key Features</h3>
<ol>
<li>Multi-role user management with granular permissions</li>
<li>Automated course allocation and approval workflows</li>
<li>Manual and automatic timetable generation</li>
<li>Exam scheduling with conflict detection</li>
<li>Venue and resource management</li>
<li>Real-time notifications and alerts</li>
<li>Comprehensive reporting and analytics</li>
<li>Audit trail for all system activities</li>
</ol>

<h3>System Workflow</h3>
<p>The system follows a hierarchical approval workflow:</p>
<ol>
<li>Course allocations created by COD</li>
<li>Approval by DVC for major decisions</li>
<li>Faculty management by Deans</li>
<li>Timetable generation by Timetable Board</li>
<li>Resource allocation by Estate Office</li>
</ol>
"""

overview_page = DocumentationPage.objects.create(
    title='System Overview & Architecture',
    slug='system-architecture-overview',
    short_description='Complete overview of the University Timetabling System',
    content=overview_content,
    category=categories['system-overview'],
    page_type='guide',
    difficulty='beginner',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='all',
    estimated_read_time=10,
    version='2.0'
)
print(f"   Created: {overview_page.title}")

# Page 2: User Roles & Permissions
roles_content = """
<h2>User Roles and Permissions System</h2>
<p>The system implements a sophisticated role-based access control (RBAC) system with automatic role creation and permission assignment.</p>

<h3>Automatic Role Creation</h3>
<p>Roles are created automatically via Django signals when the system migrates:</p>

<h3>Available Roles</h3>
<table class="table table-bordered">
<thead>
<tr>
<th>Role</th>
<th>Description</th>
<th>Key Permissions</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>DVC</strong></td>
<td>Deputy Vice Chancellor - Overall system oversight</td>
<td>Approve course allocations, assign faculty leaders</td>
</tr>
<tr>
<td><strong>DVC Admins</strong></td>
<td>Administrative support for DVC</td>
<td>Add/change faculties, manage faculty data</td>
</tr>
<tr>
<td><strong>Dean</strong></td>
<td>Faculty head - Manages departments in their faculty</td>
<td>Assign department leaders, manage departments</td>
</tr>
<tr>
<td><strong>Dean Admins</strong></td>
<td>Administrative support for Deans</td>
<td>Change department information</td>
</tr>
<tr>
<td><strong>COD</strong></td>
<td>Chair of Department - Department-level management</td>
<td>Create course allocations, forward to DVC/Timetable</td>
</tr>
<tr>
<td><strong>COD Admins</strong></td>
<td>Administrative support for COD</td>
<td>Add/change course allocations</td>
</tr>
<tr>
<td><strong>Director Timetable</strong></td>
<td>Head of Timetabling Department</td>
<td>Approve timetables, manage scheduling</td>
</tr>
<tr>
<td><strong>Timetable Admins</strong></td>
<td>Timetable department staff</td>
<td>Manage timetable entries, generate schedules</td>
</tr>
<tr>
<td><strong>Department Users</strong></td>
<td>General department staff</td>
<td>View timetables, access department data</td>
</tr>
</tbody>
</table>

<h3>Permission Mapping</h3>
<p>Each role has specific permissions defined in the ROLE_PERMISSION_MAP dictionary:</p>
"""

roles_page = DocumentationPage.objects.create(
    title='User Roles & Permissions Guide',
    slug='user-roles-permissions-guide',
    short_description='Complete guide to system roles and their permissions',
    content=roles_content,
    category=categories['user-roles-permissions'],
    page_type='reference',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='all',
    estimated_read_time=15,
    version='1.5'
)
print(f"   Created: {roles_page.title}")

# Page 3: Course Allocation Workflow
course_content = """
<h2>Course Allocation Workflow</h2>
<p>The course allocation process is the foundation of the timetabling system, involving multiple departments and approval levels.</p>

<h3>Workflow Steps</h3>
<ol>
<li><strong>Course Master Upload</strong>
<ul>
<li>Admin uploads course master data</li>
<li>System creates ProgramCourse entries</li>
<li>Courses are associated with programs and departments</li>
</ul>
</li>

<li><strong>COD Creates Allocations</strong>
<ul>
<li>COD assigns lecturers to courses</li>
<li>Specifies number of students</li>
<li>Sets origin department for cross-listed courses</li>
</ul>
</li>

<li><strong>DVC Approval Process</strong>
<ul>
<li>COD submits allocations to DVC</li>
<li>DVC reviews and approves/rejects</li>
<li>Rejections require detailed reasons</li>
</ul>
</li>

<li><strong>Timetable Submission</strong>
<ul>
<li>Approved allocations submitted to timetable department</li>
<li>SubmissionControl regulates workflow</li>
<li>COD can forward directly in urgent cases</li>
</ul>
</li>
</ol>

<h3>CourseAllocation Model Fields</h3>
<p>The CourseAllocation model tracks:</p>
<ul>
<li>Course code and name</li>
<li>Allocating and origin departments</li>
<li>Program association</li>
<li>Assigned lecturer (from Lecturer model)</li>
<li>Number of students</li>
<li>Approval status (DVC approval)</li>
<li>Submission status to timetable</li>
</ul>

<h3>Status Labels</h3>
<p>Each allocation shows a status label:</p>
<ul>
<li>⏳ Pending DVC Decision</li>
<li>✅ Approved by DVC</li>
<li>❌ Rejected by DVC - [Reason]</li>
<li>📅 Submitted to Timetable</li>
</ul>
"""

course_page = DocumentationPage.objects.create(
    title='Course Allocation Process',
    slug='course-allocation-process',
    short_description='Step-by-step guide to creating and managing course allocations',
    content=course_content,
    category=categories['course-management'],
    page_type='process',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=12,
    version='2.1'
)
print(f"   Created: {course_page.title}")

# Page 4: Timetable Generation
timetable_content = """
<h2>Timetable Generation Process</h2>
<p>The timetable generation module supports both manual and automatic scheduling with comprehensive conflict detection.</p>

<h3>Timetable Types</h3>
<ol>
<li><strong>Main Class Timetable</strong>
<ul>
<li>Regular class schedules</li>
<li>Generated per semester</li>
<li>Includes venue allocations</li>
</ul>
</li>
<li><strong>Examination Timetable</strong>
<ul>
<li>Exam schedules with specific dates</li>
<li>Includes lab exams</li>
<li>Supports merged courses</li>
</ul>
</li>
<li><strong>Lab Timetable</strong>
<ul>
<li>Separate scheduling for lab sessions</li>
<li>Lab-specific venues and equipment</li>
</ul>
</li>
</ol>

<h3>Generation Methods</h3>
<h4>Automatic Generation</h4>
<p>The system can automatically generate timetables based on:</p>
<ul>
<li>SchedulerConfig settings (start/end times, slot sizes)</li>
<li>Venue availability and capacity</li>
<li>Lecturer availability constraints</li>
<li>Course conflicts and prerequisites</li>
</ul>

<h4>Manual Creation</h4>
<p>Timetable administrators can manually:</p>
<ul>
<li>Create timetable entries</li>
<li>Drag-and-drop scheduling interface</li>
<li>Adjust venue assignments</li>
<li>Resolve conflicts manually</li>
</ul>

<h3>Export Options</h3>
<ul>
<li><strong>PDF Export</strong>: Printable timetables for departments</li>
<li><strong>CSV Export</strong>: Data for external systems</li>
<li><strong>Excel Reports</strong>: Comprehensive reports with analytics</li>
<li><strong>Calendar Feeds</strong>: iCal format for personal calendars</li>
</ul>

<h3>Publishing Workflow</h3>
<ol>
<li>Generate draft timetable (TempTimetable)</li>
<li>Review and adjust for conflicts</li>
<li>Submit for Director approval</li>
<li>Publish to departments</li>
<li>Archive previous semester timetables</li>
</ol>
"""

timetable_page = DocumentationPage.objects.create(
    title='Timetable Generation Guide',
    slug='timetable-generation-guide',
    short_description='Complete guide to generating and managing timetables',
    content=timetable_content,
    category=categories['timetable-generation'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=20,
    version='3.0'
)
print(f"   Created: {timetable_page.title}")

# Page 5: Examination Management
exam_content = """
<h2>Examination Management System</h2>
<p>The examination module handles all aspects of exam scheduling, from venue allocation to conflict resolution.</p>

<h3>Exam Scheduling Components</h3>
<ol>
<li><strong>ExamSchedulerConfig</strong>
<ul>
<li>Defines exam period dates</li>
<li>Sets time slots and durations</li>
<li>Excludes holidays and weekends</li>
<li>Configures venue capacity ratios</li>
</ul>
</li>
<li><strong>ExamTimetable</strong>
<ul>
<li>Final approved exam schedule</li>
<li>Includes date, time, and venue</li>
<li>Viewable by all departments</li>
</ul>
</li>
<li><strong>ExamTempTimetable</strong>
<ul>
<li>Draft exam schedule</li>
<li>Used for planning and adjustments</li>
<li>Unique constraints prevent double-booking</li>
</ul>
</li>
</ol>

<h3>Special Features</h3>
<h4>Shared Venue Exam Groups</h4>
<p>Allows multiple courses to share one venue during the same time slot:</p>
<ul>
<li>Prevents venue double-booking</li>
<li>Tracks total students across shared courses</li>
<li>Requires specific permission for setup</li>
</ul>

<h4>Merged Course Groups</h4>
<p>Automatically merges similar courses for combined exams:</p>
<ul>
<li>Groups courses with same base code (e.g., COSC312, COSC312(A))</li>
<li>Calculates total student count</li>
<li>Assigns appropriate venue based on capacity</li>
</ul>

<h4>Auto-Merged Exam Groups</h4>
<p>System automatically detects and merges courses during auto-scheduling.</p>

<h3>Lab Examinations</h3>
<p>Separate scheduling for lab-based exams:</p>
<ul>
<li>LabExamTimetable model</li>
<li>Lab-specific venue requirements</li>
<li>Equipment and resource tracking</li>
</ul>
"""

exam_page = DocumentationPage.objects.create(
    title='Examination Scheduling Guide',
    slug='examination-scheduling-guide',
    short_description='Complete guide to exam scheduling and management',
    content=exam_content,
    category=categories['examination-management'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=18,
    version='2.5'
)
print(f"   Created: {exam_page.title}")

# Page 6: Resource Management
resource_content = """
<h2>Resource and Venue Management</h2>
<p>Comprehensive management of physical resources including buildings, venues, and lab facilities.</p>

<h3>Resource Hierarchy</h3>
<ol>
<li><strong>Faculty</strong>
<ul>
<li>Top-level organizational unit</li>
<li>Managed by DVC and Dean</li>
<li>Can have multiple buildings</li>
</ul>
</li>
<li><strong>Building</strong>
<ul>
<li>Physical building structure</li>
<li>Belongs to a faculty (optional)</li>
<li>Has unique code (e.g., BSL, SRP)</li>
</ul>
</li>
<li><strong>Venue</strong>
<ul>
<li>Specific room or hall</li>
<li>Located in a building</li>
<li>Has capacity and description</li>
</ul>
</li>
<li><strong>Lab Venue</strong>
<ul>
<li>Specialized laboratory facilities</li>
<li>Separate from regular venues</li>
<li>Tracks equipment and resources</li>
</ul>
</li>
</ol>

<h3>Management Roles</h3>
<h4>Estate/Utilities Office</h4>
<p>Primary responsibility for:</p>
<ul>
<li>Creating and updating building records</li>
<li>Managing venue capacities and features</li>
<li>Tracking lab equipment and resources</li>
<li>Maintaining venue availability calendars</li>
</ul>

<h4>Academic Affairs</h4>
<p>Manages academic resources:</p>
<ul>
<li>Program and course structures</li>
<li>Course prerequisite chains</li>
<li>Academic calendar integration</li>
</ul>

<h3>Venue Allocation Logic</h3>
<p>The system uses smart venue allocation:</p>
<ul>
<li>Capacity matching (courses get appropriately sized venues)</li>
<li>Proximity optimization (minimize student movement)</li>
<li>Special requirements (labs, projectors, etc.)</li>
<li>Conflict avoidance (prevent double-booking)</li>
</ul>
"""

resource_page = DocumentationPage.objects.create(
    title='Resource Management Guide',
    slug='resource-management-guide',
    short_description='Managing buildings, venues, and academic resources',
    content=resource_content,
    category=categories['resource-management'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=15,
    version='2.0'
)
print(f"   Created: {resource_page.title}")

# Page 7: Submission Control Workflow
workflow_content = """
<h2>Submission Control and Workflow Management</h2>
<p>The system implements controlled submission workflows to regulate the flow of data between departments.</p>

<h3>SubmissionControl Model</h3>
<p>Each department has submission controls that regulate:</p>
<ul>
<li><strong>allow_submission_to_dvc</strong>: Controls if COD can submit to DVC</li>
<li><strong>allow_submission_to_tt</strong>: Controls if COD can submit to Timetable</li>
</ul>

<h3>Workflow Scenarios</h3>
<h4>Normal Workflow</h4>
<ol>
<li>COD creates course allocations</li>
<li>Submits to DVC for approval (if allowed)</li>
<li>DVC approves/rejects with comments</li>
<li>Approved allocations submitted to Timetable (if allowed)</li>
<li>Timetable department schedules the courses</li>
</ol>

<h4>Emergency Workflow</h4>
<p>When immediate scheduling is needed:</p>
<ol>
<li>COD uses "forward_course_allocation" permission</li>
<li>Direct submission to timetable bypassing DVC approval</li>
<li>Requires special justification and logging</li>
</ol>

<h3>Approval Hierarchy</h3>
<div class="alert alert-info">
<p><strong>Approval Chain:</strong> COD → Dean → DVC → Timetable</p>
<p>Each level can modify, approve, or reject submissions.</p>
</div>

<h3>Notification System</h3>
<p>Automatic notifications for:</p>
<ul>
<li>New submissions awaiting approval</li>
<li>Approval/rejection decisions</li>
<li>Submission status changes</li>
<li>Workflow violations or errors</li>
</ul>
"""

workflow_page = DocumentationPage.objects.create(
    title='Workflow and Submission Control',
    slug='workflow-submission-control',
    short_description='Understanding and managing submission workflows',
    content=workflow_content,
    category=categories['workflow-approvals'],
    page_type='process',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='all',
    estimated_read_time=12,
    version='1.8'
)
print(f"   Created: {workflow_page.title}")

# Page 8: Technical Implementation
tech_content = """
<h2>Technical Implementation Details</h2>
<p>Technical reference for system administrators and developers.</p>

<h3>Database Models Overview</h3>
<h4>Core Models</h4>
<ul>
<li><strong>Faculty</strong>: Organizational unit with leader assignment</li>
<li><strong>Department</strong>: Belongs to faculty, has programs</li>
<li><strong>Lecturer</strong>: Teaching staff with optional department association</li>
<li><strong>Program</strong>: Academic program within department</li>
<li><strong>ProgramCourse</strong>: Courses within programs with year/semester</li>
</ul>

<h4>Timetable Models</h4>
<ul>
<li><strong>CourseAllocation</strong>: Core allocation with approval workflow</li>
<li><strong>Timetable/TempTimetable</strong>: Final and draft timetables</li>
<li><strong>ExamTimetable/ExamTempTimetable</strong>: Exam scheduling</li>
<li><strong>LabTimetable/LabExamTimetable</strong>: Lab session scheduling</li>
</ul>

<h4>Supporting Models</h4>
<ul>
<li><strong>Building/Venue</strong>: Physical resource management</li>
<li><strong>LabVenue</strong>: Specialized lab facilities</li>
<li><strong>ArchivedCourseAllocation</strong>: Historical allocation storage</li>
<li><strong>TimetableArchive</strong>: Semester timetable archiving</li>
</ul>

<h3>Signal Handlers</h3>
<p>The system uses Django signals for automation:</p>

<h4>post_migrate Signal</h4>
<p>Automatically creates roles and permissions:</p>

<h4>post_save/post_delete Signals</h4>
<p>Audit logging for all model changes:</p>
<ul>
<li>Tracks CREATE, UPDATE, DELETE actions</li>
<li>Stores user, timestamp, and data changes</li>
<li>Skips system models (auth, sessions, etc.)</li>
<li>Migration-safe implementation</li>
</ul>

<h3>Thread-Safe Current User</h3>
<p>For audit logging in async contexts:</p>
"""

tech_page = DocumentationPage.objects.create(
    title='Technical Reference Guide',
    slug='technical-reference-guide',
    short_description='Technical documentation for system administrators',
    content=tech_content,
    category=categories['technical-reference'],
    page_type='reference',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=25,
    version='3.2'
)
print(f"   Created: {tech_page.title}")

# Page 9: Administrator Guide
admin_content = """
<h2>System Administrator Guide</h2>
<p>Comprehensive guide for system administrators managing the timetabling system.</p>

<h3>Initial Setup</h3>
<ol>
<li><strong>Superuser Creation</strong>
<ul>
<li>Create initial admin user</li>
<li>Set up email configuration</li>
<li>Configure system settings</li>
</ul>
</li>
<li><strong>Role Configuration</strong>
<ul>
<li>Verify automatic role creation</li>
<li>Check permission assignments</li>
<li>Test role-based access</li>
</ul>
</li>
<li><strong>Data Import</strong>
<ul>
<li>Import course master data</li>
<li>Set up faculties and departments</li>
<li>Create initial user accounts</li>
</ul>
</li>
</ol>

<h3>User Management</h3>
<h4>Creating Users</h4>
<p>Admin panel allows creation of users with specific roles:</p>
<ol>
<li>Navigate to Users section</li>
<li>Create new user with username/email</li>
<li>Assign to appropriate groups (roles)</li>
<li>Set department associations if needed</li>
</ol>

<h4>Bulk Operations</h4>
<ul>
<li>CSV import of lecturers</li>
<li>Bulk course allocation</li>
<li>Mass user creation</li>
</ul>

<h3>System Configuration</h3>
<h4>Scheduler Settings</h4>
<ul>
<li>Configure time slots and durations</li>
<li>Set academic calendar dates</li>
<li>Define exclusion periods</li>
</ul>

<h4>Workflow Controls</h4>
<ul>
<li>Enable/disable submission workflows</li>
<li>Set approval thresholds</li>
<li>Configure notification rules</li>
</ul>

<h3>Maintenance Tasks</h3>
<h4>Regular Maintenance</h4>
<ul>
<li>Database backup and optimization</li>
<li>Log rotation and cleanup</li>
<li>User account auditing</li>
</ul>

<h4>Semester Transitions</h4>
<ol>
<li>Archive current allocations</li>
<li>Clear old timetable data</li>
<li>Prepare for new semester</li>
<li>Update academic year settings</li>
</ol>

<h3>Troubleshooting</h3>
<h4>Common Issues</h4>
<ul>
<li>Permission denied errors</li>
<li>Duplicate allocation errors</li>
<li>Venue conflict issues</li>
<li>Workflow submission blocks</li>
</ul>

<h4>Debug Tools</h4>
<ul>
<li>Audit log review</li>
<li>Permission testing tools</li>
<li>Database integrity checks</li>
</ul>
"""

admin_page = DocumentationPage.objects.create(
    title='System Administrator Guide',
    slug='system-administrator-guide',
    short_description='Complete guide for system administrators',
    content=admin_content,
    category=categories['administrator-guide'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=30,
    version='4.0'
)
print(f"   Created: {admin_page.title}")

# Page 10: FAQ
faq_content = """
<h2>Frequently Asked Questions</h2>
<p>Common questions and answers about the University Timetabling System.</p>

<h3>General Questions</h3>
<div class="faq-item">
<h4>Q: How do I get access to the system?</h4>
<p>A: Contact your department administrator or the system administrator. They will create your account and assign appropriate roles.</p>
</div>

<div class="faq-item">
<h4>Q: What browser should I use?</h4>
<p>A: The system works best with Chrome 80+, Firefox 75+, or Safari 13+. JavaScript must be enabled.</p>
</div>

<div class="faq-item">
<h4>Q: How often is data backed up?</h4>
<p>A: Daily automated backups with 30-day retention. Critical data has additional real-time replication.</p>
</div>

<h3>User Role Questions</h3>
<div class="faq-item">
<h4>Q: What's the difference between DVC and Dean roles?</h4>
<p>A: DVC oversees all faculties, while Dean manages only their specific faculty. DVC approves course allocations, Deans manage departments.</p>
</div>

<div class="faq-item">
<h4>Q: Can one user have multiple roles?</h4>
<p>A: Yes, users can belong to multiple groups. However, some roles may have conflicting permissions.</p>
</div>

<div class="faq-item">
<h4>Q: How are permissions assigned?</h4>
<p>A: Permissions are automatically assigned based on role groups. System administrators can modify permissions if needed.</p>
</div>

<h3>Course Allocation Questions</h3>
<div class="faq-item">
<h4>Q: What happens if I submit duplicate course allocations?</h4>
<p>A: The system validates uniqueness based on program and course code. Duplicates are rejected with an error message.</p>
</div>

<div class="faq-item">
<h4>Q: Can I edit allocations after DVC approval?</h4>
<p>A: Once approved by DVC, allocations become read-only for COD. Contact DVC admin for modifications.</p>
</div>

<div class="faq-item">
<h4>Q: How do cross-listed courses work?</h4>
<p>A: Use the origin_department field to specify which department originally offers the course.</p>
</div>

<h3>Timetable Questions</h3>
<div class="faq-item">
<h4>Q: How are timetable conflicts resolved?</h4>
<p>A: The system detects conflicts automatically. For automatic generation, it avoids conflicts. For manual creation, it warns administrators.</p>
</div>

<div class="faq-item">
<h4>Q: Can I export timetables for my department?</h4>
<p>A: Yes, multiple export formats are available: PDF, CSV, Excel, and calendar formats.</p>
</div>

<div class="faq-item">
<h4>Q: How far in advance should timetables be generated?</h4>
<p>A: We recommend generating timetables at least 2 weeks before semester start for review and adjustments.</p>
</div>

<h3>Technical Questions</h3>
<div class="faq-item">
<h4>Q: Where are audit logs stored?</h4>
<p>A: In the ActivityLog model. They can be accessed via admin panel with appropriate permissions.</p>
</div>

<div class="faq-item">
<h4>Q: How do I report a bug or issue?</h4>
<p>A: Use the feedback system or contact the technical support team at support@university.edu</p>
</div>

<div class="faq-item">
<h4>Q: Is there an API for system integration?</h4>
<p>A: Yes, REST API endpoints are available for approved integrations. Contact system administrators for access.</p>
</div>

<h3>Troubleshooting</h3>
<div class="faq-item">
<h4>Q: I get "Permission Denied" error. What should I do?</h4>
<p>A: Contact your department administrator to verify your role assignments and permissions.</p>
</div>

<div class="faq-item">
<h4>Q: The system is slow when generating timetables. Is this normal?</h4>
<p>A: Complex timetable generation with many constraints can take time. Large schedules may take several minutes.</p>
</div>

<div class="faq-item">
<h4>Q: I can't submit allocations. What could be wrong?</h4>
<p>A: Check SubmissionControl settings for your department. The workflow may be temporarily disabled.</p>
</div>
"""

faq_page = DocumentationPage.objects.create(
    title='FAQ & Troubleshooting Guide',
    slug='faq-troubleshooting-guide',
    short_description='Frequently asked questions and troubleshooting',
    content=faq_content,
    category=categories['faq-troubleshooting'],
    page_type='faq',
    difficulty='beginner',
    order=1,
    is_published=True,
    author=author,
    requires_login=False,
    access_level='all',
    estimated_read_time=20,
    version='2.3'
)
print(f"   Created: {faq_page.title}")

# ============================================================
# 4. CREATE SECTIONS WITH CODE EXAMPLES
# ============================================================

print("\n4. CREATING SECTIONS WITH CODE EXAMPLES...")

# Section for Technical Reference - Signal Handlers
tech_section = DocumentationSection.objects.create(
    page=tech_page,
    title='Signal Handler Implementation',
    content='''
<h3>Automatic Role and Permission Seeding</h3>
<p>The system automatically creates roles and assigns permissions using Django''s post_migrate signal.</p>
''',
    order=1,
    is_active=True,
    slug='signal-handler-implementation'
)
print(f"   Created section: {tech_section.title}")

# Code Example 1: Role Permission Map
code_example1 = CodeExample.objects.create(
    section=tech_section,
    title='ROLE_PERMISSION_MAP Configuration',
    code='''ROLE_PERMISSION_MAP = {
    "dvc": [
        "approve_course_allocation",
        "assign_faculty_leader",
        "remove_faculty_leader",
    ],
    "dvc_admins": [
        "add_faculty",
        "change_faculty",
    ],
    "dean": [
        "assign_department_leader",
        "remove_department_leader",
    ],
    # ... more roles
}''',
    language='python',
    order=1,
    description='Dictionary mapping roles to their permission codenames'
)

# Code Example 2: Signal Handler
code_example2 = CodeExample.objects.create(
    section=tech_section,
    title='post_migrate Signal Handler',
    code='''@receiver(post_migrate)
def sync_groups_and_permissions(sender, **kwargs):
    if sender.name == "core":
        seed_roles_and_permissions()
        print("✅ Groups & permissions synced")''',
    language='python',
    order=2,
    description='Signal handler that runs after migrations to sync roles'
)

# Code Example 3: Thread-safe Current User
code_example3 = CodeExample.objects.create(
    section=tech_section,
    title='Thread-safe Current User Implementation',
    code='''_user_local = threading.local()

def set_current_user(user):
    _user_local.user = user

def get_current_user():
    return getattr(_user_local, "user", None)''',
    language='python',
    order=3,
    description='Thread-local storage for current user in async contexts'
)

# Section for Course Allocation Model
course_section = DocumentationSection.objects.create(
    page=course_page,
    title='CourseAllocation Model Definition',
    content='''
<h3>Database Model Structure</h3>
<p>The CourseAllocation model defines how courses are allocated to lecturers and tracks approval status.</p>
''',
    order=2,
    is_active=True,
    slug='courseallocation-model-definition'
)
print(f"   Created section: {course_section.title}")

# Code Example 4: CourseAllocation Model
code_example4 = CodeExample.objects.create(
    section=course_section,
    title='CourseAllocation Model Snippet',
    code='''class CourseAllocation(models.Model):
    course_code = models.CharField(max_length=20)
    course_name = models.CharField(max_length=200)
    department = models.ForeignKey("Department", on_delete=models.CASCADE)
    origin_department = models.ForeignKey("Department", on_delete=models.SET_NULL, null=True)
    program = models.ForeignKey("Program", on_delete=models.CASCADE)
    lecturer = models.ForeignKey("Lecturer", on_delete=models.SET_NULL, null=True)
    number_of_students = models.PositiveIntegerField(default=0)
    approved_by_dvc = models.BooleanField(default=False)
    rejected_by_dvc = models.BooleanField(default=False)
    reason_for_disapproval = models.TextField(blank=True, default="No reason yet")
    submitted_to_tt = models.BooleanField(default=False)''',
    language='python',
    order=1,
    description='Core CourseAllocation model fields and relationships'
)

# Section for Audit Logging
admin_section = DocumentationSection.objects.create(
    page=admin_page,
    title='Audit Logging Implementation',
    content='''
<h3>Automatic Activity Tracking</h3>
<p>The system automatically logs all CREATE, UPDATE, and DELETE operations on models.</p>
''',
    order=3,
    is_active=True,
    slug='audit-logging-implementation'
)
print(f"   Created section: {admin_section.title}")

# Code Example 5: Audit Log Signal
code_example5 = CodeExample.objects.create(
    section=admin_section,
    title='Model Save Signal Handler',
    code='''@receiver(post_save)
def log_model_save(sender, instance, created, **kwargs):
    if sender._meta.app_label in ["auth", "admin", "contenttypes", "sessions"]:
        return
    
    action = "CREATE" if created else "UPDATE"
    log_activity(action, instance)''',
    language='python',
    order=1,
    description='Signal handler that logs all model save operations'
)

# ============================================================
# 5. ASSIGN TAGS TO PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO PAGES...")

# Define tag assignments
tag_assignments = {
    overview_page: ['system-architecture', 'user-guide'],
    roles_page: ['dvc', 'dean', 'cod', 'timetable', 'user-guide'],
    course_page: ['course-allocation', 'workflow', 'cod'],
    timetable_page: ['timetable', 'technical'],
    exam_page: ['examination', 'timetable'],
    resource_page: ['venue-management', 'administration'],
    workflow_page: ['workflow', 'dvc', 'dean', 'cod'],
    tech_page: ['technical', 'system-architecture'],
    admin_page: ['administration', 'technical'],
    faq_page: ['user-guide'],
}

for page, tag_slugs in tag_assignments.items():
    for tag_slug in tag_slugs:
        if tag_slug in tags:
            PageTag.objects.get_or_create(
                page=page,
                tag=tags[tag_slug]
            )
    print(f"   Assigned {len(tag_slugs)} tags to: {page.title}")

# ============================================================
# 6. CREATE INTERNAL LINKS BETWEEN PAGES
# ============================================================

print("\n6. CREATING INTERNAL LINKS...")

# Define internal links
internal_links = [
    (overview_page, roles_page, "Learn about user roles and permissions"),
    (roles_page, course_page, "See how COD manages course allocations"),
    (course_page, workflow_page, "Understand the approval workflow"),
    (timetable_page, exam_page, "Learn about exam scheduling"),
    (exam_page, resource_page, "Manage exam venues and resources"),
    (admin_page, tech_page, "Technical implementation details"),
    (faq_page, roles_page, "Role-specific questions"),
]

for from_page, to_page, description in internal_links:
    from documentation.models import InternalLink
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
print("DOCUMENTATION POPULATION COMPLETE")
print("=" * 80)

# Count statistics
categories_count = DocumentationCategory.objects.count()
pages_count = DocumentationPage.objects.count()
sections_count = DocumentationSection.objects.count()
tags_count = DocumentationTag.objects.count()
code_examples_count = CodeExample.objects.count()

print(f"""
📊 DOCUMENTATION STATISTICS:
   Categories:      {categories_count}
   Pages:           {pages_count}
   Sections:        {sections_count}
   Tags:            {tags_count}
   Code Examples:   {code_examples_count}

🔗 ACCESS DOCUMENTATION:
   Main URL:        /documentation/
   By Category:     /documentation/category/{categories['system-overview'].slug}/
   Search:          /documentation/search/

👥 USER GUIDES:
   - System Overview: Complete system introduction
   - User Roles: Detailed role descriptions and permissions
   - Course Allocation: Step-by-step allocation process
   - Timetable Generation: Manual and automatic scheduling
   - Examination Management: Exam scheduling guide
   - Resource Management: Venue and building management
   - Workflow Guide: Approval processes and controls
   - Technical Reference: Implementation details
   - Administrator Guide: System administration
   - FAQ: Common questions and troubleshooting

🚀 NEXT STEPS:
   1. Review documentation at /documentation/
   2. Test user access with different roles
   3. Update documentation as system evolves
   4. Add department-specific documentation as needed

✅ Documentation successfully populated into database!
""")

print("=" * 80)
print("Documentation is now available in the system database.")
print("Access it via the Documentation menu or /documentation/ URL.")
print("=" * 80)