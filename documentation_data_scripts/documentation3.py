#!/usr/bin/env python
"""
UNIVERSITY TIMETABLING SYSTEM - COD PANEL DOCUMENTATION
This script adds comprehensive documentation for the COD Panel, course allocation,
and related functionality in the timetabling system.
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
    CodeExample, DocumentationTag, PageTag, InternalLink
)
from django.contrib.auth.models import User
from django.utils import timezone
import re

print("=" * 80)
print("UNIVERSITY TIMETABLING SYSTEM - COD PANEL DOCUMENTATION")
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
# 1. CHECK AND CREATE CATEGORIES FOR COD FUNCTIONALITY
# ============================================================

print("\n1. CHECKING AND CREATING CATEGORIES FOR COD FUNCTIONALITY...")

cod_categories = [
    {
        'name': 'COD Panel & Course Allocation',
        'slug': 'cod-panel-course-allocation',
        'description': 'Complete guide to the COD Panel for managing courses and allocations',
        'icon': 'fas fa-user-tie',
        'order': 16,
        'access_level': 'department',
    },
    {
        'name': 'Lecturer Management',
        'slug': 'lecturer-management',
        'description': 'Managing lecturer profiles, assignments, and department associations',
        'icon': 'fas fa-chalkboard-teacher',
        'order': 17,
        'access_level': 'department',
    },
    {
        'name': 'Program & Course Management',
        'slug': 'program-course-management',
        'description': 'Managing academic programs, courses, and curriculum structure',
        'icon': 'fas fa-graduation-cap',
        'order': 18,
        'access_level': 'department',
    },
    {
        'name': 'Lab & Practical Allocation',
        'slug': 'lab-practical-allocation',
        'description': 'Allocating laboratory sessions and practical facilities',
        'icon': 'fas fa-flask',
        'order': 19,
        'access_level': 'department',
    },
    {
        'name': 'Submission Workflow',
        'slug': 'submission-workflow',
        'description': 'Course submission workflow to DVC and Timetable Department',
        'icon': 'fas fa-paper-plane',
        'order': 20,
        'access_level': 'department',
    },
]

categories = {}
for cat_data in cod_categories:
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

cod_tags = [
    {'name': 'COD Panel', 'slug': 'cod-panel', 'color': '#28a745'},
    {'name': 'Course Allocation', 'slug': 'course-allocation', 'color': '#6f42c1'},
    {'name': 'Lecturer Management', 'slug': 'lecturer-management', 'color': '#17a2b8'},
    {'name': 'Program Management', 'slug': 'program-management', 'color': '#fd7e14'},
    {'name': 'Lab Allocation', 'slug': 'lab-allocation', 'color': '#e83e8c'},
    {'name': 'Submission Control', 'slug': 'submission-control', 'color': '#20c997'},
    {'name': 'Workflow', 'slug': 'workflow', 'color': '#6c757d'},
    {'name': 'Department Management', 'slug': 'department-management', 'color': '#6610f2'},
]

tags = {}
for tag_data in cod_tags:
    tag, created = DocumentationTag.objects.get_or_create(
        slug=tag_data['slug'],
        defaults=tag_data
    )
    tags[tag_data['slug']] = tag
    status = "Created" if created else "Exists"
    print(f"   {status}: {tag.name}")

# Also get existing tags for reference
existing_tags = {}
for tag in DocumentationTag.objects.all():
    existing_tags[tag.slug] = tag

# ============================================================
# 3. CREATE COD PANEL DOCUMENTATION PAGES
# ============================================================

print("\n3. CREATING COD PANEL DOCUMENTATION PAGES...")

# Page 1: COD Panel Overview
cod_panel_content = """
<h2>COD Panel - Chair of Department Panel</h2>
<p>The COD Panel is the primary interface for Department Chairs to manage all academic scheduling activities within their department.</p>

<h3>Key Responsibilities</h3>
<p>As a COD, you are responsible for:</p>
<ol>
<li><strong>Course Allocation Management</strong>: Assigning courses to lecturers</li>
<li><strong>Program Management</strong>: Overseeing department programs and courses</li>
<li><strong>Lecturer Management</strong>: Managing lecturer profiles and assignments</li>
<li><strong>Lab Allocation</strong>: Scheduling laboratory sessions</li>
<li><strong>Submission Workflow</strong>: Submitting allocations for approval</li>
<li><strong>Data Validation</strong>: Ensuring data accuracy and completeness</li>
</ol>

<h3>Access and Permissions</h3>
<h4>User Roles with COD Access</h4>
<ul>
<li><strong>COD (Chair of Department)</strong>: Full access to department management</li>
<li><strong>COD Admins</strong>: Administrative support with limited permissions</li>
</ul>

<h4>Department Detection</h4>
<p>The system automatically detects your department based on:</p>
<ol>
<li><strong>Department Leader Assignment</strong>: If you are assigned as department leader</li>
<li><strong>Lecturer Profile</strong>: If you have a lecturer profile with department association</li>
<li><strong>OrgRole Mapping</strong>: Through organizational role assignments</li>
<li><strong>Manual Selection</strong>: Fallback to manual department selection</li>
</ol>

<h3>Panel Layout</h3>
<h4>Main Sections</h4>
<ol>
<li><strong>Course Allocations</strong>: Primary allocation management interface</li>
<li><strong>Program Management</strong>: Programs and courses structure</li>
<li><strong>Lecturer Management</strong>: Lecturer profiles and assignments</li>
<li><strong>Lab Allocations</strong>: Laboratory session scheduling</li>
<li><strong>Submission Controls</strong>: Workflow management tools</li>
</ol>

<h4>Navigation</h4>
<ul>
<li><strong>Sidebar Navigation</strong>: Quick access to all sections</li>
<li><strong>Breadcrumb Trails</strong>: Contextual navigation path</li>
<li><strong>Quick Actions</strong>: Frequently used functions</li>
<li><strong>Status Indicators</strong>: Real-time status updates</li>
</ul>

<h3>Department Context</h3>
<p>The COD Panel operates within your department context:</p>
<ul>
<li><strong>Department-Specific Data</strong>: Only shows data from your department</li>
<li><strong>Cross-Department Courses</strong>: Handle courses offered by other departments</li>
<li><strong>Origin Department Tracking</strong>: Track where courses originate from</li>
<li><strong>Faculty Alignment</strong>: Department belongs to specific faculty</li>
</ul>

<h3>Data Management Principles</h3>
<h4>Data Integrity</h4>
<ul>
<li><strong>Unique Constraints</strong>: Prevent duplicate course allocations</li>
<li><strong>Validation Rules</strong>: Ensure data consistency and completeness</li>
<li><strong>Referential Integrity</strong>: Maintain relationships between entities</li>
<li><strong>Audit Trails</strong>: Track all changes and modifications</li>
</ul>

<h4>Data Security</h4>
<ul>
<li><strong>Role-Based Access</strong>: Strict permission controls</li>
<li><strong>Department Isolation</strong>: Data isolation between departments</li>
<li><strong>Encryption</strong>: Secure data storage and transmission</li>
<li><strong>Activity Logging</strong>: Comprehensive audit logs</li>
</ul>

<h3>Getting Started</h3>
<h4>Initial Setup</h4>
<ol>
<li><strong>Department Verification</strong>: Confirm your department assignment</li>
<li><strong>Program Review</strong>: Review existing programs and courses</li>
<li><strong>Lecturer Profiles</strong>: Verify lecturer information</li>
<li><strong>Course Master Data</strong>: Review course master upload</li>
<li><strong>System Configuration</strong>: Configure department settings</li>
</ol>

<h4>First Tasks</h4>
<ol>
<li><strong>Review Existing Allocations</strong>: Check current course allocations</li>
<li><strong>Update Lecturer Assignments</strong>: Assign lecturers to courses</li>
<li><strong>Create New Allocations</strong>: Add new course allocations</li>
<li><strong>Submit for Approval</strong>: Submit allocations to DVC</li>
<li><strong>Generate Reports</strong>: Create allocation reports</li>
</ol>

<h3>Best Practices</h3>
<ol>
<li><strong>Regular Updates</strong>: Update allocations regularly throughout semester</li>
<li><strong>Data Validation</strong>: Verify all data before submission</li>
<li><strong>Communication</strong>: Communicate changes to affected parties</li>
<li><strong>Backup</strong>: Regular data backup and verification</li>
<li><strong>Documentation</strong>: Document allocation decisions and rationale</li>
</ol>
"""

cod_panel_page = DocumentationPage.objects.create(
    title='COD Panel Overview',
    slug='cod-panel-overview',
    short_description='Complete guide to the COD Panel for department chairs',
    content=cod_panel_content,
    category=categories['cod-panel-course-allocation'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=25,
    version='2.0'
)
print(f"   Created: {cod_panel_page.title}")

# Page 2: Course Allocation Management
course_allocation_content = """
<h2>Course Allocation Management</h2>
<p>Comprehensive guide to managing course allocations in the COD Panel.</p>

<h3>Allocation Concepts</h3>
<h4>What is Course Allocation?</h4>
<p>Course allocation involves assigning courses to lecturers, specifying student numbers, and setting departmental relationships.</p>

<h4>Key Components</h4>
<ul>
<li><strong>Course Code</strong>: Unique identifier for the course (e.g., COSC101)</li>
<li><strong>Course Name</strong>: Full descriptive name of the course</li>
<li><strong>Department</strong>: Department responsible for the allocation</li>
<li><strong>Origin Department</strong>: Department that originally offers the course</li>
<li><strong>Program</strong>: Academic program the course belongs to</li>
<li><strong>Lecturer</strong>: Assigned teaching staff</li>
<li><strong>Student Count</strong>: Number of students enrolled</li>
</ul>

<h3>Allocation Workflow</h3>
<h4>Step-by-Step Process</h4>
<ol>
<li><strong>Course Selection</strong>: Select or create course from program courses</li>
<li><strong>Department Assignment</strong>: Assign to allocating and origin departments</li>
<li><strong>Program Association</strong>: Link to academic program</li>
<li><strong>Lecturer Assignment</strong>: Assign lecturer or create new lecturer</li>
<li><strong>Student Count</strong>: Specify number of students</li>
<li><strong>Validation</strong>: System validates allocation constraints</li>
<li><strong>Save</strong>: Save allocation to database</li>
</ol>

<h4>Group Course Handling</h4>
<p>For courses with multiple sections/groups:</p>
<ul>
<li><strong>Group Suffix Detection</strong>: Automatically detects -A, -B suffixes</li>
<li><strong>Base Code Recognition</strong>: Identifies base course code</li>
<li><strong>Group Validation</strong>: Prevents duplicate group assignments</li>
<li><strong>Group Selection</strong>: Interface for group letter selection</li>
</ul>

<h3>Allocation Interface</h3>
<h4>Main Allocation Form</h4>
<p>The allocation form includes:</p>
<ul>
<li><strong>Course Code Field</strong>: Auto-complete with validation</li>
<li><strong>Course Name Auto-fill</strong>: Automatically fills from course master</li>
<li><strong>Department Selectors</strong>: Allocating and origin departments</li>
<li><strong>Program Selection</strong>: Filtered by department</li>
<li><strong>Lecturer Search</strong>: Search and select lecturers</li>
<li><strong>Student Count</strong>: Numeric input with validation</li>
<li><strong>Action Buttons</strong>: Save, cancel, delete operations</li>
</ul>

<h4>Advanced Features</h4>
<ul>
<li><strong>Duplicate Detection</strong>: Prevents duplicate allocations</li>
<li><strong>Conflict Checking</strong>: Checks for scheduling conflicts</li>
<li><strong>Auto-suggest</strong>: Suggests based on historical data</li>
<li><strong>Bulk Operations</strong>: Support for multiple allocations</li>
<li><strong>Import/Export</strong>: Data import and export capabilities</li>
</ul>

<h3>Validation Rules</h3>
<h4>Data Validation</h4>
<ul>
<li><strong>Required Fields</strong>: All essential fields must be filled</li>
<li><strong>Course Code Format</strong>: Valid course code format</li>
<li><strong>Student Count Range</strong>: Reasonable student numbers</li>
<li><strong>Lecturer Availability</strong>: Check lecturer assignment limits</li>
<li><strong>Department Consistency</strong>: Logical department relationships</li>
</ul>

<h4>Business Rules</h4>
<ul>
<li><strong>Unique Allocation</strong>: Unique course code per program</li>
<li><strong>Group Assignment</strong>: Proper group suffix assignment</li>
<li><strong>Cross-Department Rules</strong>: Rules for inter-department courses</li>
<li><strong>Approval Status</strong>: Restrictions based on approval status</li>
</ul>

<h3>Allocation Status</h3>
<h4>Status Types</h4>
<ul>
<li><strong>Draft</strong>: Newly created, not submitted</li>
<li><strong>Pending DVC Approval</strong>: Submitted to DVC for approval</li>
<li><strong>Approved by DVC</strong>: Approved by Deputy Vice Chancellor</li>
<li><strong>Rejected by DVC</strong>: Rejected with reason</li>
<li><strong>Submitted to Timetable</strong>: Forwarded to timetable department</li>
</ul>

<h4>Status Indicators</h4>
<p>Visual indicators for each status:</p>
<ul>
<li>⏳ <strong>Pending DVC Decision</strong>: Waiting for DVC approval</li>
<li>✅ <strong>Approved by DVC</strong>: Approved and ready for scheduling</li>
<li>❌ <strong>Rejected by DVC</strong>: Rejected - needs revision</li>
<li>📅 <strong>Submitted to Timetable</strong>: With timetable department</li>
</ul>

<h3>Rejected Course Management</h3>
<h4>Handling Rejections</h4>
<ol>
<li><strong>Review Rejection Reason</strong>: Understand why course was rejected</li>
<li><strong>Make Corrections</strong>: Address issues identified by DVC</li>
<li><strong>Restore Allocation</strong>: Move back to pending status</li>
<li><strong>Resubmit</strong>: Submit corrected allocation</li>
</ol>

<h4>Rejection Reasons</h4>
<p>Common rejection reasons include:</p>
<ul>
<li><strong>Incomplete Information</strong>: Missing required data</li>
<li><strong>Data Inconsistencies</strong>: Conflicting information</li>
<li><strong>Policy Violations</strong>: Violates university policies</li>
<li><strong>Resource Constraints</strong>: Insufficient resources</li>
<li><strong>Scheduling Conflicts</strong>: Conflict with other courses</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Plan Ahead</strong>: Start allocations early in semester</li>
<li><strong>Verify Data</strong> Double-check all information before submission</li>
<li><strong>Communicate</strong>: Inform lecturers of their assignments</li>
<li><strong>Document Decisions</strong>: Keep records of allocation decisions</li>
<li><strong>Review Regularly</strong>: Regular review of allocation status</li>
<li><strong>Backup Data</strong>: Regular backups of allocation data</li>
</ol>
"""

course_allocation_page = DocumentationPage.objects.create(
    title='Course Allocation Management Guide',
    slug='course-allocation-management-guide',
    short_description='Complete guide to creating and managing course allocations',
    content=course_allocation_content,
    category=categories['cod-panel-course-allocation'],
    page_type='guide',
    difficulty='intermediate',
    order=2,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=20,
    version='1.8'
)
print(f"   Created: {course_allocation_page.title}")

# Page 3: Lecturer Management
lecturer_management_content = """
<h2>Lecturer Management System</h2>
<p>Comprehensive guide to managing lecturer profiles and assignments in the COD Panel.</p>

<h3>Lecturer Profile Components</h3>
<h4>Core Information</h4>
<ul>
<li><strong>Payroll Number</strong>: Unique identifier (e.g., LEC0001)</li>
<li><strong>Name</strong>: Full name of lecturer</li>
<li><strong>Email</strong>: Official email address</li>
<li><strong>Designation</strong>: Academic title (Prof, Dr, Mr, Ms, Mrs)</li>
<li><strong>Department Association</strong>: Primary department affiliation</li>
<li><strong>User Account</strong>: Linked system user account</li>
</ul>

<h4>Derived Information</h4>
<ul>
<li><strong>Display Name</strong>: Formatted name for display purposes</li>
<li><strong>Course Assignments</strong>: Courses currently assigned</li>
<li><strong>Workload Summary</strong>: Teaching load information</li>
<li><strong>Availability Status</strong>: Current availability</li>
</ul>

<h3>Lecturer Creation</h3>
<h4>Manual Creation</h4>
<ol>
<li><strong>Access Lecturer Panel</strong>: Navigate to lecturer management</li>
<li><strong>Click "Add Lecturer"</strong>: Open creation form</li>
<li><strong>Enter Basic Information</strong>: Name, email, designation</li>
<li><strong>Assign Department</strong>: Select primary department</li>
<li><strong>Generate Payroll</strong>: System generates unique payroll number</li>
<li><strong>Create User Account</strong>: System creates linked user account</li>
<li><strong>Save</strong>: Save lecturer profile</li>
</ol>

<h4>Automatic Features</h4>
<ul>
<li><strong>Auto-generate Payroll</strong>: Unique payroll number generation</li>
<li><strong>Auto-create User</strong>: Automatic user account creation</li>
<li><strong>Email Formatting</strong>: Standard email format generation</li>
<li><strong>Name Normalization</strong>: Consistent name formatting</li>
</ul>

<h3>Lecturer Assignment</h3>
<h4>Course Assignment Process</h4>
<ol>
<li><strong>Search Lecturers</strong>: Search existing lecturers</li>
<li><strong>Select Lecturer</strong>: Choose from search results</li>
<li><strong>Create New Lecturer</strong>: If lecturer doesn't exist</li>
<li><strong>Assign to Course</strong>: Link lecturer to course allocation</li>
<li><strong>Validate Assignment</strong>: Check assignment constraints</li>
<li><strong>Save Allocation</strong>: Save course allocation</li>
</ol>

<h4>Assignment Constraints</h4>
<ul>
<li><strong>Workload Limits</strong>: Maximum courses per lecturer</li>
<li><strong>Availability Conflicts</strong>: Time and schedule conflicts</li>
<li><strong>Department Alignment</strong>: Department compatibility</li>
<li><strong>Expertise Matching</strong>: Subject matter expertise</li>
</ul>

<h3>Lecturer Search and Selection</h3>
<h4>Search Interface</h4>
<ul>
<li><strong>Name Search</strong>: Search by lecturer name</li>
<li><strong>Department Filter</strong>: Filter by department</li>
<li><strong>Designation Filter</strong>: Filter by academic title</li>
<li><strong>Live Search</strong>: Real-time search results</li>
<li><strong>Auto-complete</strong>: Predictive text suggestions</li>
</ul>

<h4>Selection Options</h4>
<ul>
<li><strong>Existing Lecturer</strong>: Select from existing profiles</li>
<li><strong>Create New</strong>: Create new lecturer profile</li>
<li><strong>Quick Create</strong>: Simplified creation process</li>
<li><strong>Import from CSV</strong>: Bulk import lecturers</li>
</ul>

<h3>User Account Integration</h3>
<h4>Automatic User Creation</h4>
<p>When creating a lecturer:</p>
<ol>
<li><strong>Username Generation</strong>: Based on email address</li>
<li><strong>Password Setting</strong>: Initial password set to payroll number</li>
<li><strong>Email Configuration</strong>: Email set to lecturer email</li>
<li><strong>Name Mapping</strong>: First/last name extracted</li>
<li><strong>Role Assignment</strong>: Assigned to lecturer role group</li>
</ol>

<h4>Account Synchronization</h4>
<ul>
<li><strong>Auto-sync Updates</strong>: Changes sync to user account</li>
<li><strong>Email Updates</strong>: Email changes update username</li>
<li><strong>Name Synchronization</strong>: Name changes sync to user profile</li>
<li><strong>Account Disabling</strong>: Lecturer deactivation disables account</li>
</ul>

<h3>Department Association</h3>
<h4>Primary Department</h4>
<p>Each lecturer has a primary department:</p>
<ul>
<li><strong>Assignment Basis</strong>: Determines default assignments</li>
<li><strong>Workload Calculation</strong>: Department-specific workload</li>
<li><strong>Reporting</strong>: Department-based reporting</li>
<li><strong>Access Control</strong>: Department-specific data access</li>
</ul>

<h4>Cross-Department Teaching</h4>
<p>Lecturers can teach in multiple departments:</p>
<ul>
<li><strong>Course-Based Assignment</strong>: Assigned per course allocation</li>
<li><strong>Temporary Assignment</strong>: Temporary department assignment</li>
<li><strong>Joint Appointment</strong>: Formal multiple department affiliation</li>
</ul>

<h3>Management Functions</h3>
<h4>CRUD Operations</h4>
<ul>
<li><strong>Create</strong>: Add new lecturer profiles</li>
<li><strong>Read</strong>: View lecturer information</li>
<li><strong>Update</strong>: Modify existing profiles</li>
<li><strong>Delete</strong>: Remove lecturer profiles</li>
<li><strong>Search</strong>: Find lecturers</li>
<li><strong>Filter</strong>: Filter lecturer lists</li>
</ul>

<h4>Bulk Operations</h4>
<ul>
<li><strong>Bulk Import</strong>: Import multiple lecturers via CSV</li>
<li><strong>Bulk Update</strong>: Update multiple profiles</li>
<li><strong>Bulk Assignment</strong>: Assign multiple courses</li>
<li><strong>Bulk Export</strong>: Export lecturer data</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Complete Profiles</strong>: Ensure all lecturer information is complete</li>
<li><strong>Regular Updates</strong>: Keep lecturer information current</li>
<li><strong>Validation</strong>: Verify email and contact information</li>
<li><strong>Workload Monitoring</strong>: Monitor teaching loads</li>
<li><strong>Communication</strong>: Regular communication with lecturers</li>
<li><strong>Documentation</strong>: Document assignment decisions</li>
</ol>
"""

lecturer_management_page = DocumentationPage.objects.create(
    title='Lecturer Management Guide',
    slug='lecturer-management-guide',
    short_description='Complete guide to managing lecturer profiles and assignments',
    content=lecturer_management_content,
    category=categories['lecturer-management'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=18,
    version='1.5'
)
print(f"   Created: {lecturer_management_page.title}")

# Page 4: Program and Course Management
program_management_content = """
<h2>Program and Course Management</h2>
<p>Comprehensive guide to managing academic programs and courses in the COD Panel.</p>

<h3>Program Structure</h3>
<h4>Program Components</h4>
<ul>
<li><strong>Program Name</strong>: Official program name</li>
<li><strong>Department Association</strong>: Offering department</li>
<li><strong>Description</strong>: Program overview and objectives</li>
<li><strong>Course List</strong>: Courses within the program</li>
<li><strong>Duration</strong>: Program duration in years</li>
</ul>

<h4>Program Hierarchy</h4>
<ol>
<li><strong>Faculty</strong>: Highest level organizational unit</li>
<li><strong>Department</strong>: Academic department within faculty</li>
<li><strong>Program</strong>: Degree or diploma program</li>
<li><strong>Course</strong>: Individual courses within program</li>
<li><strong>Allocation</strong>: Course allocation to lecturers</li>
</ol>

<h3>Course Management</h3>
<h4>Course Components</h4>
<ul>
<li><strong>Course Code</strong>: Unique identifier (e.g., COSC101)</li>
<li><strong>Course Name</strong>: Descriptive course title</li>
<li><strong>Academic Year</strong>: Year in program (1-6)</li>
<li><strong>Semester</strong>: Semester offering (1 or 2)</li>
<li><strong>Program Association</strong>: Parent program</li>
<li><strong>Credit Hours</strong>: Credit value</li>
</ul>

<h4>Course Code Normalization</h4>
<p>The system normalizes course codes:</p>
<ol>
<li><strong>Space Handling</strong>: Converts "CS 101" to "CS101"</li>
<li><strong>Case Normalization</strong>: Converts to uppercase</li>
<li><strong>Group Suffix Detection</strong>: Identifies -A, -B suffixes</li>
<li><strong>Base Code Extraction</strong>: Extracts base course code</li>
</ol>

<h3>Program Creation and Management</h3>
<h4>Creating Programs</h4>
<ol>
<li><strong>Select Department</strong>: Choose offering department</li>
<li><strong>Enter Program Details</strong>: Name and description</li>
<li><strong>Save Program</strong>: Create program record</li>
<li><strong>Add Courses</strong>: Add courses to program</li>
<li><strong>Configure Structure</strong>: Set year/semester structure</li>
</ol>

<h4>Program Operations</h4>
<ul>
<li><strong>Create</strong>: Add new programs</li>
<li><strong>Edit</strong>: Modify existing programs</li>
<li><strong>Delete</strong>: Remove programs</li>
<li><strong>Clone</strong>: Duplicate program structure</li>
<li><strong>Export</strong>: Export program data</li>
</ul>

<h3>Course Creation and Management</h3>
<h4>Creating Courses</h4>
<ol>
<li><strong>Select Program</strong>: Choose parent program</li>
<li><strong>Enter Course Code</strong>: Unique course identifier</li>
<li><strong>Enter Course Name</strong>: Descriptive course title</li>
<li><strong>Set Academic Level</strong>: Year and semester</li>
<li><strong>Save Course</strong>: Create course record</li>
</ol>

<h4>Course Operations</h4>
<ul>
<li><strong>Create</strong>: Add new courses</li>
<li><strong>Edit</strong>: Modify course details</li>
<li><strong>Delete</strong>: Remove courses</li>
<li><strong>Move</strong>: Move between programs</li>
<li><strong>Bulk Operations</strong>: Multiple course operations</li>
</ul>

<h3>Academic Year and Semester Management</h3>
<h4>Year Levels</h4>
<p>Courses are assigned to academic years (1-6):</p>
<ul>
<li><strong>Year 1-2</strong>: Foundational courses</li>
<li><strong>Year 3-4</strong>: Intermediate courses</li>
<li><strong>Year 5-6</strong>: Advanced/Postgraduate courses</li>
</ul>

<h4>Semester System</h4>
<ul>
<li><strong>Semester 1</strong>: First semester courses</li>
<li><strong>Semester 2</strong>: Second semester courses</li>
<li><strong>Year-Long Courses</strong>: Span both semesters</li>
</ul>

<h3>Course Validation and Constraints</h3>
<h4>Validation Rules</h4>
<ul>
<li><strong>Unique Course Code</strong>: Unique within program</li>
<li><strong>Valid Year Range</strong>: Year must be 1-6</li>
<li><strong>Valid Semester</strong>: Semester must be 1 or 2</li>
<li><strong>Program Association</strong>: Must belong to program</li>
<li><strong>Department Consistency</strong>: Consistent with program department</li>
</ul>

<h4>Business Rules</h4>
<ul>
<li><strong>Prerequisite Chains</strong>: Course prerequisite relationships</li>
<li><strong>Credit Load Limits</strong>: Maximum credits per semester</li>
<li><strong>Year Progression</strong>: Logical year progression</li>
<li><strong>Course Offerings</strong>: Regular/irregular offerings</li>
</ul>

<h3>Integration with Course Allocation</h3>
<h4>Course Selection in Allocations</h4>
<p>When creating allocations:</p>
<ol>
<li><strong>Program Selection</strong>: Select program first</li>
<li><strong>Course Auto-complete</strong>: Course codes auto-suggest</li>
<li><strong>Course Name Auto-fill</strong>: Course name auto-populated</li>
<li><strong>Year/Semester Auto-fill</strong>: Academic level auto-filled</li>
</ol>

<h4>Data Synchronization</h4>
<ul>
<li><strong>Real-time Updates</strong>: Changes reflect immediately</li>
<li><strong>Validation Integration</strong>: Allocation validates against course data</li>
<li><strong>Consistency Checks</strong>: Ensures data consistency</li>
<li><strong>Audit Trail</strong>: Tracks all changes</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Standardized Naming</strong>: Consistent course naming conventions</li>
<li><strong>Regular Reviews</strong>: Regular program and course reviews</li>
<li><strong>Documentation</strong>: Document program structures</li>
<li><strong>Stakeholder Input</strong>: Involve faculty in program design</li>
<li><strong>Compliance</strong>: Ensure regulatory compliance</li>
<li><strong>Future Planning</strong>: Plan for program evolution</li>
</ol>
"""

program_management_page = DocumentationPage.objects.create(
    title='Program and Course Management Guide',
    slug='program-course-management-guide',
    short_description='Complete guide to managing academic programs and courses',
    content=program_management_content,
    category=categories['program-course-management'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=20,
    version='1.6'
)
print(f"   Created: {program_management_page.title}")

# Page 5: Lab Allocation Management
lab_allocation_content = """
<h2>Lab Allocation Management</h2>
<p>Comprehensive guide to managing laboratory allocations in the COD Panel.</p>

<h3>Lab Allocation Concepts</h3>
<h4>What is Lab Allocation?</h4>
<p>Lab allocation involves assigning laboratory sessions to courses, specifying lab venues, and allocating resources.</p>

<h4>Key Components</h4>
<ul>
<li><strong>Program Course</strong>: Course requiring lab sessions</li>
<li><strong>Lab Venue</strong>: Specialized laboratory facility</li>
<li><strong>Lab Instructor</strong>: Assigned lab lecturer</li>
<li><strong>Student Count</strong>: Number of students in lab</li>
<li><strong>Session Details</strong>: Date, time, duration</li>
<li><strong>Equipment Requirements</strong>: Special equipment needs</li>
</ul>

<h3>Lab Venue Management</h3>
<h4>Lab Venue Attributes</h4>
<ul>
<li><strong>Venue Code</strong>: Unique identifier (e.g., CS_LAB_1)</li>
<li><strong>Capacity</strong>: Maximum student capacity</li>
<li><strong>Equipment List</strong>: Available equipment and resources</li>
<li><strong>Description</strong>: Venue specifications and notes</li>
<li><strong>Safety Features</strong>: Safety equipment and features</li>
</ul>

<h4>Venue Types</h4>
<ul>
<li><strong>Computer Labs</strong>: Computer-based laboratories</li>
<li><strong>Science Labs</strong>: Chemistry, physics, biology labs</li>
<li><strong>Engineering Labs</strong>: Engineering and technical labs</li>
<li><strong>Specialized Labs</strong>: Special equipment labs</li>
<li><strong>Multipurpose Labs</strong>: Flexible use laboratories</li>
</ul>

<h3>Lab Allocation Workflow</h3>
<h4>Step-by-Step Process</h4>
<ol>
<li><strong>Select Program Course</strong>: Choose course needing lab</li>
<li><strong>Select Lab Venue</strong>: Choose appropriate laboratory</li>
<li><strong>Assign Lab Instructor</strong>: Assign lecturer for lab</li>
<li><strong>Set Student Count</strong>: Specify number of students</li>
<li><strong>Schedule Session</strong>: Set date and time (if applicable)</li>
<li><strong>Specify Requirements</strong>: Note special requirements</li>
<li><strong>Save Allocation</strong>: Save lab allocation</li>
</ol>

<h4>Timetable Integration</h4>
<ul>
<li><strong>Lab Timetable</strong>: Separate lab timetable</li>
<li><strong>Conflict Checking</strong>: Checks for lab conflicts</li>
<li><strong>Resource Scheduling</strong>: Equipment and resource scheduling</li>
<li><strong>Export Integration</strong>: Included in timetable exports</li>
</ul>

<h3>Lab Allocation Interface</h3>
<h4>Main Allocation Form</h4>
<ul>
<li><strong>Program Selection</strong>: Filter by academic program</li>
<li><strong>Course Selection</strong>: Select program course</li>
<li><strong>Lab Venue Selection</strong>: Choose from available labs</li>
<li><strong>Instructor Assignment</strong>: Assign lab instructor</li>
<li><strong>Student Count</strong>: Specify lab group size</li>
<li><strong>Notes Field</strong>: Additional requirements</li>
<li><strong>Action Buttons</strong>: Save, update, delete</li>
</ul>

<h4>Advanced Features</h4>
<ul>
<li><strong>Capacity Validation</strong>: Checks venue capacity</li>
<li><strong>Equipment Validation</strong>: Verifies equipment availability</li>
<li><strong>Instructor Availability</strong>: Checks instructor schedule</li>
<li><strong>Recurring Sessions</strong>: Support for recurring labs</li>
<li><strong>Bulk Allocation</strong>: Multiple lab allocations</li>
</ul>

<h3>Lab Scheduling Considerations</h3>
<h4>Timing Constraints</h4>
<ul>
<li><strong>Session Duration</strong>: Standard lab session lengths</li>
<li><strong>Setup Time</strong>: Lab setup and preparation time</li>
<li><strong>Cleanup Time</strong>: Post-lab cleanup time</li>
<li><strong>Safety Breaks</strong>: Safety protocol requirements</li>
</ul>

<h4>Resource Management</h4>
<ul>
<li><strong>Equipment Sharing</strong>: Shared equipment scheduling</li>
<li><strong>Consumable Tracking</strong>: Lab consumables management</li>
<li><strong>Maintenance Scheduling</strong>: Lab maintenance periods</li>
<li><strong>Safety Inspections</strong>: Regular safety checks</li>
</ul>

<h3>Lab Exam Scheduling</h3>
<h4>Exam-Specific Considerations</h4>
<ul>
<li><strong>Extended Duration</strong>: Longer exam sessions</li>
<li><strong>Reduced Capacity</strong>: Lower student counts for exams</li>
<li><strong>Additional Supervision</strong>: Extra invigilation requirements</li>
<li><strong>Special Equipment</strong>: Exam-specific equipment</li>
</ul>

<h4>Integration with Main Exams</h4>
<ul>
<li><strong>Coordinated Scheduling</strong>: Coordination with written exams</li>
<li><strong>Venue Allocation</strong>: Dedicated exam lab venues</li>
<li><strong>Resource Allocation</strong>: Exam-specific resources</li>
<li><strong>Timetable Integration</strong>: Unified exam timetable</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Early Planning</strong>: Plan lab allocations early</li>
<li><strong>Capacity Management</strong>: Respect venue capacities</li>
<li><strong>Equipment Verification</strong>: Verify equipment availability</li>
<li><strong>Safety Compliance</strong>: Adhere to safety regulations</li>
<li><strong>Communication</strong>: Clear communication with lab staff</li>
<li><strong>Documentation</strong> Document lab allocation decisions</li>
</ol>
"""

lab_allocation_page = DocumentationPage.objects.create(
    title='Lab Allocation Management Guide',
    slug='lab-allocation-management-guide',
    short_description='Complete guide to managing laboratory allocations',
    content=lab_allocation_content,
    category=categories['lab-practical-allocation'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=18,
    version='1.4'
)
print(f"   Created: {lab_allocation_page.title}")

# Page 6: Submission Workflow
submission_workflow_content = """
<h2>Submission Workflow Management</h2>
<p>Comprehensive guide to the course allocation submission workflow in the COD Panel.</p>

<h3>Workflow Overview</h3>
<h4>Submission Stages</h4>
<ol>
<li><strong>Draft Stage</strong>: Initial allocation creation</li>
<li><strong>DVC Submission</strong>: Submit to Deputy Vice Chancellor</li>
<li><strong>DVC Review</strong>: DVC approval/rejection</li>
<li><strong>Timetable Submission</strong>: Forward to timetable department</li>
<li><strong>Final Scheduling</strong>: Timetable generation</li>
</ol>

<h4>Key Decision Points</h4>
<ul>
<li><strong>COD Decision</strong>: When to submit allocations</li>
<li><strong>DVC Decision</strong>: Approve, reject, or modify</li>
<li><strong>Timetable Decision</strong>: Schedule generation timing</li>
<li><strong>Publication Decision</strong>: When to publish timetable</li>
</ul>

<h3>SubmissionControl System</h3>
<h4>Control Components</h4>
<ul>
<li><strong>Department Scoping</strong>: Controls per department</li>
<li><strong>DVC Submission Control</strong>: Enable/disable DVC submission</li>
<li><strong>Timetable Submission Control</strong>: Enable/disable TT submission</li>
<li><strong>Audit Tracking</strong>: Toggle history and audit</li>
</ul>

<h4>Control Interface</h4>
<ul>
<li><strong>Toggle Switches</strong>: Simple on/off controls</li>
<li><strong>Department Selection</strong>: Department-specific controls</li>
<li><strong>Status Indicators</strong>: Current control status</li>
<li><strong>Audit Logs</strong>: Control change history</li>
</ul>

<h3>DVC Submission Process</h3>
<h4>Submission Preparation</h4>
<ol>
<li><strong>Allocation Completion</strong>: Complete all allocations</li>
<li><strong>Data Validation</strong> Verify all allocation data</li>
<li><strong>Workload Review</strong>: Review lecturer workloads</li>
<li><strong>Conflict Checking</strong>: Check for scheduling conflicts</li>
<li><strong>Documentation</strong>: Prepare supporting documentation</li>
</ol>

<h4>Submission Execution</h4>
<ol>
<li><strong>Enable Submission</strong>: Ensure DVC submission is enabled</li>
<li><strong>Select Allocations</strong>: Choose allocations to submit</li>
<li><strong>Submit Batch</strong>: Submit allocations as batch</li>
<li><strong>Confirmation</strong>: Receive submission confirmation</li>
<li><strong>Status Update</strong>: Allocations move to pending DVC</li>
</ol>

<h3>DVC Review and Response</h3>
<h4>Approval Process</h4>
<ul>
<li><strong>Review Period</strong>: DVC review timeframe</li>
<li><strong>Approval Criteria</strong>: Criteria for approval</li>
<li><strong>Approval Notification</strong>: Notification of approval</li>
<li><strong>Status Update</strong>: Move to approved status</li>
</ul>

<h4>Rejection Process</h4>
<ul>
<li><strong>Rejection Reasons</strong>: Common rejection reasons</li>
<li><strong>Rejection Notification</strong>: Notification of rejection</li>
<li><strong>Reason Documentation</strong>: Detailed rejection reasons</li>
<li><strong>Correction Guidance</strong>: Guidance for corrections</li>
</ul>

<h3>Timetable Submission</h3>
<h4>Submission to Timetable Department</h4>
<ol>
<li><strong>DVC Approval Check</strong>: Ensure DVC approval</li>
<li><strong>Timetable Enablement</strong>: Enable TT submission</li>
<li><strong>Batch Selection</strong>: Select approved allocations</li>
<li><strong>Forward to TT</strong>: Submit to timetable department</li>
<li><strong>Confirmation</strong>: Receive submission confirmation</li>
</ol>

<h4>Timetable Department Processing</h4>
<ul>
<li><strong>Data Import</strong>: Import allocation data</li>
<li><strong>Schedule Generation</strong>: Generate timetable</li>
<li><strong>Conflict Resolution</strong>: Resolve scheduling conflicts</li>
<li><strong>Publication</strong>: Publish final timetable</li>
</ul>

<h3>Emergency Workflow</h3>
<h4>Direct Timetable Submission</h4>
<p>For urgent allocations:</p>
<ol>
<li><strong>Emergency Justification</strong>: Provide urgent need justification</li>
<li><strong>Direct Forward</strong>: Use direct forward permission</li>
<li><strong>Bypass DVC</strong>: Skip DVC approval step</li>
<li><strong>Enhanced Logging</strong>: Detailed audit logging</li>
<li><strong>Post-facto Review</strong>: Review after submission</li>
</ol>

<h4>Emergency Controls</h4>
<ul>
<li><strong>Permission Requirements</strong>: Special permissions needed</li>
<li><strong>Justification Requirements</strong>: Mandatory justification</li>
<li><strong>Approval Chain</strong>: Alternative approval chain</li>
<li><strong>Audit Requirements</strong>: Enhanced audit tracking</li>
</ul>

<h3>Status Tracking and Monitoring</h3>
<h4>Status Types</h4>
<ul>
<li><strong>Draft</strong>: Not yet submitted</li>
<li><strong>Pending DVC</strong>: Awaiting DVC approval</li>
<li><strong>Approved by DVC</strong>: DVC approved</li>
<li><strong>Rejected by DVC</strong>: DVC rejected</li>
<li><strong>Submitted to TT</strong>: With timetable department</li>
<li><strong>Scheduled</strong>: Included in timetable</li>
</ul>

<h4>Monitoring Tools</h4>
<ul>
<li><strong>Status Dashboard</strong>: Real-time status display</li>
<li><strong>Notification System</strong>: Status change notifications</li>
<li><strong>Report Generation</strong>: Status reports</li>
<li><strong>Audit Trail</strong>: Complete activity history</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Early Submission</strong>: Submit well before deadlines</li>
<li><strong>Complete Documentation</strong>: Thorough supporting documentation</li>
<li><strong>Regular Follow-up</strong>: Regular status checking</li>
<li><strong>Clear Communication</strong>: Clear communication with stakeholders</li>
<li><strong>Backup Planning</strong>: Contingency plans for delays</li>
<li><strong>Process Compliance</strong>: Adhere to established processes</li>
</ol>
"""

submission_workflow_page = DocumentationPage.objects.create(
    title='Submission Workflow Management Guide',
    slug='submission-workflow-management-guide',
    short_description='Complete guide to the course allocation submission workflow',
    content=submission_workflow_content,
    category=categories['submission-workflow'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=22,
    version='1.7'
)
print(f"   Created: {submission_workflow_page.title}")

# ============================================================
# 4. CREATE SECTIONS WITH DETAILED CODE EXAMPLES
# ============================================================

print("\n4. CREATING SECTIONS WITH DETAILED CODE EXAMPLES...")

# Section 1: COD Panel Main View
cod_panel_section = DocumentationSection.objects.create(
    page=cod_panel_page,
    title='COD Panel Implementation Details',
    content='''
<h3>Main COD Panel View Function</h3>
<p>The COD panel is implemented as a comprehensive Django view that handles both GET and POST requests with AJAX support.</p>
''',
    order=1,
    is_active=True,
    slug='cod-panel-implementation'
)
print(f"   Created section: {cod_panel_section.title}")

# Code Example 1: COD Panel Main View
code_example_cod1 = CodeExample.objects.create(
    section=cod_panel_section,
    title='COD Panel Main View Function',
    code='''@group_required("COD", "COD Admins")
@login_required
def cod_panel(request):
    """
    Main view for COD panel. Supports AJAX endpoints via POST:
     - action=get_program_courses
     - action=create_program_course
     - action=create_allocation
     - action=delete_allocation
     - action=allocation_detail
     - action=list_allocations
     - action=search_lecturers
     - action=create_program
    """

    # -----------------------
    # Handle AJAX POST
    # -----------------------
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action")

        # ✅ Handle rejected course actions
        if action in ["list_rejected", "restore_rejected", "delete_rejected"]:
            return RejectedCourseService.handle(request, action)

        # -----------------------
        # Return program courses
        # -----------------------
        if action == "get_program_courses":
            program_id = request.POST.get("program_id")
            department_id = request.POST.get("department_id")

            qs = ProgramCourse.objects.select_related("program")
            if program_id:
                qs = qs.filter(program_id=program_id)
            elif department_id:
                qs = qs.filter(program__department_id=department_id)
            else:
                return JsonResponse({"status": "error", "message": "program_id or department_id required"}, status=400)

            data = []
            for pc in qs.order_by("course_code"):
                base_code, group = strip_group_suffix(pc.course_code)
                data.append({
                    "id": pc.id,
                    "program_id": pc.program.id,
                    "program_name": pc.program.name,
                    "course_code": pc.course_code,
                    "base_code": base_code,
                    "group": group,
                    "course_name": pc.course_name,
                    "year": pc.year,
                    "semester": pc.semester,
                })
            return JsonResponse({"status": "success", "program_courses": data})

        # -----------------------
        # Create ProgramCourse
        # -----------------------
        if action == "create_program_course":
            program_id = request.POST.get("program_id")
            course_code = normalize_code(request.POST.get("course_code", ""))
            course_name = request.POST.get("course_name", "").strip()
            year = request.POST.get("year")
            semester = request.POST.get("semester")

            if not (program_id and course_code and course_name and year and semester):
                return JsonResponse({"status": "error", "message": "Missing fields"}, status=400)

            program = get_object_or_404(Program, pk=program_id)

            try:
                year = int(year)
                semester = int(semester)
            except (ValueError, TypeError):
                return JsonResponse({"status": "error", "message": "Invalid year/semester"}, status=400)

            if ProgramCourse.objects.filter(program=program, course_code__iexact=course_code).exists():
                return JsonResponse({"status": "error", "message": "Program already has that course code."}, status=400)

            pc = ProgramCourse.objects.create(
                program=program,
                course_code=course_code,
                course_name=course_name,
                year=year,
                semester=semester
            )

            return JsonResponse({"status": "success", "program_course": {
                "id": pc.id,
                "program_id": program.id,
                "program_name": program.name,
                "course_code": pc.course_code,
                "course_name": pc.course_name,
                "year": pc.year,
                "semester": pc.semester,
            }})
        # ... rest of AJAX handlers ...
        
    # -----------------------
    # GET request
    # -----------------------
    detected_dept = detect_user_department(request.user)

    if detected_dept:
        allocations = (
            CourseAllocation.objects
            .select_related("department", "origin_department", "program", "lecturer")
            .filter(department=detected_dept)
        )
        # ✅ Allow all departments for origin selection
        departments = Department.objects.select_related("faculty").all()
        from django.db.models import Case, When, Value, IntegerField

        # Order lecturers: those in detected department first
        lecturers = Lecturer.objects.all().annotate(
            is_dept=Case(
                When(department=detected_dept, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
                    ).order_by("-is_dept", "name")

        programs = Program.objects.filter(department=detected_dept)
    else:
        allocations = CourseAllocation.objects.none()
        departments = Department.objects.none()
        lecturers = Lecturer.objects.none()
        programs = Program.objects.none()

    control, _ = SubmissionControl.objects.get_or_create(department=detected_dept)

    return render(request, "cod_panel.html", {
        "allocations": allocations,
        "departments": departments,
        "lecturers": lecturers,
        "programs": programs,
        "years": range(1, 7),
        "detected_dept": detected_dept,
        "allow_submission_to_dvc": control.allow_submission_to_dvc,
        "allow_submission_to_tt": control.allow_submission_to_tt,
    })''',
    language='python',
    order=1,
    description='Main COD panel view function with AJAX support'
)

# Section 2: Course Allocation Creation
course_allocation_section = DocumentationSection.objects.create(
    page=course_allocation_page,
    title='Course Allocation Implementation',
    content='''
<h3>Course Allocation Creation Logic</h3>
<p>The course allocation creation process includes sophisticated validation, group handling, and lecturer management.</p>
''',
    order=2,
    is_active=True,
    slug='course-allocation-implementation'
)
print(f"   Created section: {course_allocation_section.title}")

# Code Example 2: Course Allocation Creation
code_example_allocation1 = CodeExample.objects.create(
    section=course_allocation_section,
    title='Course Allocation Creation Function',
    code='''def create_allocation(request):
    """Handle course allocation creation with group logic"""
    allocation_id = request.POST.get("id")
    raw_course_code = request.POST.get("course_code", "").strip()
    base_course_code = normalize_code(raw_course_code.split("-", 1)[0])
    group_choice = request.POST.get("group_letter")

    if group_choice:
        course_code = append_group(base_course_code, group_choice)
    else:
        course_code = normalize_code(raw_course_code)

    course_name = request.POST.get("course_name", "").strip()
    dept_id = request.POST.get("department_id")
    origin_dept_id = request.POST.get("origin_department_id")
    program_id = request.POST.get("program_id") or None
    lecturer_id = request.POST.get("lecturer_id")
    lecturer_name = request.POST.get("lecturer_name", "").strip()
    number_of_students = request.POST.get("number_of_students", "0")

    try:
        number_of_students = int(number_of_students)
        if number_of_students < 0:
            number_of_students = 0
    except (ValueError, TypeError):
        number_of_students = 0

    if not dept_id:
        return JsonResponse({"status": "error", "message": "Department is required."}, status=400)

    department = get_object_or_404(Department, pk=dept_id)
    origin_department = get_object_or_404(Department, pk=origin_dept_id) if origin_dept_id else None
    program = get_object_or_404(Program, pk=program_id) if program_id else None

    # -----------------------
    # Lecturer Handling
    # -----------------------
    clean_name = re.sub(r"\\s+", "", lecturer_name.lower())
    lecturer_obj = None

    if lecturer_id and lecturer_id not in ["", "__new__"]:
        lecturer_obj = get_object_or_404(Lecturer, pk=lecturer_id)
    elif lecturer_id == "__new__" and lecturer_name:
        lecturer_obj, _ = Lecturer.objects.get_or_create(
            name=lecturer_name,
            defaults={
                "payroll_number": generate_unique_payroll(),
                "email": f"{clean_name}@example.com",
                "designation": "Mr"
            }
        )

    # duplication check
    qs = CourseAllocation.objects.filter(program=program, course_code__iexact=course_code)
    if allocation_id:
        qs = qs.exclude(pk=allocation_id)
    if qs.exists():
        return JsonResponse({"status": "error", "message": "This course code is already allocated to that program."}, status=400)

    # group logic
    base, existing_group = strip_group_suffix(course_code)
    similar_qs = CourseAllocation.objects.filter(
        course_code__iregex=rf"^{re.escape(base)}(?:[-/]\\s*[A-Z])?$"
    )
    if allocation_id:
        similar_qs = similar_qs.exclude(pk=allocation_id)
    existing_groups = {strip_group_suffix(s.course_code)[1] or "A" for s in similar_qs}

    if not strip_group_suffix(course_code)[1] and existing_groups:
        return JsonResponse({
            "status": "need_group",
            "message": "Course code exists in allocation database. Please choose a group letter.",
            "existing_groups": sorted(list(existing_groups))
        }, status=409)

    if strip_group_suffix(course_code)[1] in existing_groups:
        return JsonResponse({"status": "error", "message": f"Group {strip_group_suffix(course_code)[1]} already exists."}, status=400)

    # save
    with transaction.atomic():
        if allocation_id:
            allocation = get_object_or_404(CourseAllocation, pk=allocation_id)
            allocation.course_code = course_code
            allocation.course_name = course_name
            allocation.department = department
            allocation.origin_department = origin_department
            allocation.program = program
            allocation.lecturer = lecturer_obj
            allocation.number_of_students = number_of_students
            allocation.full_clean()
            allocation.save()
        else:
            allocation = CourseAllocation.objects.create(
                course_code=course_code,
                course_name=course_name,
                department=department,
                origin_department=origin_department,
                program=program,
                lecturer=lecturer_obj,
                number_of_students=number_of_students
            )

    return JsonResponse({
        "status": "success",
        "allocation": {
            "id": allocation.id,
            "course_code": allocation.course_code,
            "course_name": allocation.course_name,
            "department": allocation.department.name,
            "origin_department": allocation.origin_department.name if allocation.origin_department else "",
            "program": allocation.program.name if allocation.program else "",
            "lecturer": allocation.lecturer.display_name if allocation.lecturer else "",
            "number_of_students": allocation.number_of_students
        }
    })''',
    language='python',
    order=1,
    description='Course allocation creation with group handling and validation'
)

# Section 3: Helper Functions
helper_functions_section = DocumentationSection.objects.create(
    page=cod_panel_page,
    title='Helper Functions and Utilities',
    content='''
<h3>Utility Functions for COD Panel</h3>
<p>The COD panel uses several helper functions for data processing and validation.</p>
''',
    order=3,
    is_active=True,
    slug='helper-functions'
)
print(f"   Created section: {helper_functions_section.title}")

# Code Example 3: Helper Functions
code_example_helper1 = CodeExample.objects.create(
    section=helper_functions_section,
    title='Course Code Normalization and Group Handling',
    code='''import re
from typing import Optional, Tuple

def normalize_code(code: str) -> str:
    """
    Normalize course code format.
    Example: "CS 101" → "CS101", "cosc101" → "COSC101"
    """
    if not code:
        return ""
    code = code.strip().upper()
    # Insert space between letters and digits: e.g., CS101 → CS 101
    code = re.sub(r"^([A-Z]+)(\\d+)", r"\\1 \\2", code)
    return code

def strip_group_suffix(code: str) -> Tuple[str, Optional[str]]:
    """
    If code ends with -A or /A (group suffix), return (base_code, group_letter)
    else (code, None)
    Examples:
        "COSC101-A" → ("COSC101", "A")
        "COSC101" → ("COSC101", None)
        "MATH201/B" → ("MATH201", "B")
    """
    m = re.match(r"^(.*?)(?:[-/]\\s*([A-Z]))\\s*$", code, re.I)
    if m:
        base = m.group(1).strip()
        return base, m.group(2).upper()
    return code, None

def append_group(code: str, group_letter: str) -> str:
    """
    Append group suffix to course code.
    Example: "COSC101", "A" → "COSC101-A"
    """
    code = code.strip()
    return f"{code}-{group_letter.upper()}"

def detect_user_department(user: User) -> Optional[Department]:
    """
    Try several heuristics to find the department associated with the logged-in user:
     - Department.leader == user
     - Lecturer with email matching user.email
     - OrgRole with title containing 'COD'
    """
    try:
        dept = Department.objects.filter(leader=user).first()
        if dept:
            return dept
    except Exception:
        pass

    # Try Lecturer by email
    try:
        lect = Lecturer.objects.filter(email__iexact=(user.email or "")).first()
        if lect:
            # if Lecturer had a department FK: return lect.department
            pass
    except Exception:
        pass

    # Try OrgRole
    try:
        org = getattr(user, "org_role", None)
        if org and "COD" in org.title.upper():
            parts = org.title.split("-", 1)
            if len(parts) > 1:
                dept_name = parts[1].strip()
                return Department.objects.filter(name__icontains=dept_name).first()
    except Exception:
        pass

    return None

def generate_unique_payroll():
    """Generate unique payroll like LEC0001, LEC0002 ..."""
    last = Lecturer.objects.order_by("-id").first()
    if not last:
        return "LEC0001"
    try:
        last_num = int(last.payroll_number.replace("LEC", ""))
    except Exception:
        last_num = last.id
    return f"LEC{last_num + 1:04d}"''',
    language='python',
    order=1,
    description='Utility functions for course code processing and department detection'
)

# Section 4: Submission Control
submission_control_section = DocumentationSection.objects.create(
    page=submission_workflow_page,
    title='Submission Control Implementation',
    content='''
<h3>Submission Control System Implementation</h3>
<p>The submission control system manages workflow permissions at the department level.</p>
''',
    order=4,
    is_active=True,
    slug='submission-control-implementation'
)
print(f"   Created section: {submission_control_section.title}")

# Code Example 4: Submission Control Functions
code_example_submission1 = CodeExample.objects.create(
    section=submission_control_section,
    title='Submission Control Toggle Functions',
    code='''from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404

@login_required
def toggle_submission_to_dvc(request):
    """
    Toggle whether CODs can submit allocations to DVC (scoped to department).
    """
    detected_dept = Department.objects.filter(leader=request.user).first()
    if not detected_dept:
        return JsonResponse({"status": "error", "msg": "Not a department leader."}, status=403)

    control, _ = SubmissionControl.objects.get_or_create(department=detected_dept)
    control.allow_submission_to_dvc = not control.allow_submission_to_dvc
    control.save()

    return JsonResponse({
        "status": "success",
        "allow_submission_to_dvc": control.allow_submission_to_dvc,
        "department": detected_dept.name
    })

@login_required
def toggle_submission_to_tt(request):
    """
    Toggle whether DVC-approved allocations can be forwarded to timetable (scoped to department).
    """
    detected_dept = Department.objects.filter(leader=request.user).first()
    if not detected_dept:
        return JsonResponse({"status": "error", "msg": "Not a department leader."}, status=403)

    control, _ = SubmissionControl.objects.get_or_create(department=detected_dept)
    control.allow_submission_to_tt = not control.allow_submission_to_tt
    control.save()

    return JsonResponse({
        "status": "success",
        "allow_submission_to_tt": control.allow_submission_to_tt,
        "department": detected_dept.name
    })

def get_control():
    """Always return the single SubmissionControl instance."""
    return SubmissionControl.objects.first() or SubmissionControl.objects.create()

class SubmissionControl(models.Model):
    """
    Submission workflow switches, scoped per Department.
    """
    department = models.OneToOneField(
        "Department",
        on_delete=models.CASCADE,
        related_name="submission_control",
        null=True,
        blank=True
    )
    allow_submission_to_dvc = models.BooleanField(default=True)
    allow_submission_to_tt = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        dept_name = self.department.name if self.department else "GLOBAL (legacy)"
        return f"{dept_name} -> DVC: {self.allow_submission_to_dvc}, TT: {self.allow_submission_to_tt}"

    class Meta:
        verbose_name = "Submission Control"
        verbose_name_plural = "Submission Controls"''',
    language='python',
    order=1,
    description='Submission control functions and model definition'
)

# Section 5: Rejected Course Service
rejected_course_section = DocumentationSection.objects.create(
    page=course_allocation_page,
    title='Rejected Course Service Implementation',
    content='''
<h3>Rejected Course Management Service</h3>
<p>The RejectedCourseService handles rejected course actions including listing, restoration, and deletion.</p>
''',
    order=5,
    is_active=True,
    slug='rejected-course-service'
)
print(f"   Created section: {rejected_course_section.title}")

# Code Example 5: Rejected Course Service
code_example_rejected1 = CodeExample.objects.create(
    section=rejected_course_section,
    title='RejectedCourseService Class',
    code='''from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from TT_APP.models import CourseAllocation

class RejectedCourseService:
    """
    Service to handle rejected course actions (list, restore, delete).
    Reusable in COD panel, DVC panel, or APIs.
    """

    @staticmethod
    def list_rejected(request):
        """Return rejected courses for the logged-in user's department."""
        dept = detect_user_department(request.user)
        rejected_qs = CourseAllocation.objects.filter(
            department=dept,
            rejected_by_dvc=True
        )
        data = [{
            "id": a.id,
            "course_code": a.course_code,
            "course_name": a.course_name,
            "program": a.program.name if a.program else "",
            "lecturer": a.lecturer.display_name if a.lecturer else "",
            "reason": a.reason_for_disapproval,
        } for a in rejected_qs]

        return JsonResponse({"status": "success", "rejected": data})

    @staticmethod
    def restore_rejected(request):
        """Restore a previously rejected course back to 'Pending'."""
        pk = request.POST.get("id")
        alloc = get_object_or_404(CourseAllocation, pk=pk)
        alloc.rejected_by_dvc = False
        alloc.approved_by_dvc = False  # ✅ reset to neutral state
        alloc.reason_for_disapproval = "No reason yet"
        alloc.save()
        return JsonResponse({"status": "success", "id": pk})

    @staticmethod
    def delete_rejected(request):
        """Delete a rejected course allocation completely."""
        pk = request.POST.get("id")
        alloc = get_object_or_404(CourseAllocation, pk=pk)
        alloc.delete()
        return JsonResponse({"status": "success", "id": pk})

    @staticmethod
    def handle(request, action):
        """Dispatcher for rejected course actions."""
        if action == "list_rejected":
            return RejectedCourseService.list_rejected(request)
        elif action == "restore_rejected":
            return RejectedCourseService.restore_rejected(request)
        elif action == "delete_rejected":
            return RejectedCourseService.delete_rejected(request)
        return JsonResponse({"status": "error", "message": "Invalid rejected action"})''',
    language='python',
    order=1,
    description='Service class for managing rejected course allocations'
)

# ============================================================
# 5. ASSIGN TAGS TO PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO PAGES...")

# Define tag assignments for COD pages
cod_tag_assignments = {
    cod_panel_page: ['cod-panel', 'department-management', 'workflow'],
    course_allocation_page: ['course-allocation', 'cod-panel', 'workflow'],
    lecturer_management_page: ['lecturer-management', 'cod-panel'],
    program_management_page: ['program-management', 'course-allocation'],
    lab_allocation_page: ['lab-allocation', 'cod-panel'],
    submission_workflow_page: ['submission-control', 'workflow', 'cod-panel'],
}

for page, tag_slugs in cod_tag_assignments.items():
    for tag_slug in tag_slugs:
        if tag_slug in tags or tag_slug in existing_tags:
            tag = tags.get(tag_slug) or existing_tags.get(tag_slug)
            PageTag.objects.get_or_create(
                page=page,
                tag=tag
            )
    print(f"   Assigned {len(tag_slugs)} tags to: {page.title}")

# ============================================================
# 6. CREATE INTERNAL LINKS BETWEEN PAGES
# ============================================================

print("\n6. CREATING INTERNAL LINKS BETWEEN PAGES...")

# Define internal links for COD documentation
cod_internal_links = [
    # Link COD pages together
    (cod_panel_page, course_allocation_page, "Manage course allocations"),
    (cod_panel_page, lecturer_management_page, "Manage lecturer profiles"),
    (cod_panel_page, program_management_page, "Manage programs and courses"),
    (cod_panel_page, lab_allocation_page, "Manage lab allocations"),
    (cod_panel_page, submission_workflow_page, "Manage submission workflow"),
    
    # Link to related existing pages
    (course_allocation_page, 'auto-scheduling-system-guide', "Auto-schedule allocated courses"),
    (submission_workflow_page, 'timetable-generation-guide', "Generate timetables from approved allocations"),
    (lecturer_management_page, 'user-roles-permissions-guide', "Understand user roles and permissions"),
    
    # Cross-links between COD pages
    (course_allocation_page, lecturer_management_page, "Assign lecturers to courses"),
    (program_management_page, course_allocation_page, "Allocate courses from programs"),
    (lab_allocation_page, course_allocation_page, "Link lab sessions to course allocations"),
]

# Helper function to get page by slug
def get_page_by_slug(slug):
    try:
        return DocumentationPage.objects.get(slug=slug)
    except DocumentationPage.DoesNotExist:
        return None

for link_data in cod_internal_links:
    from_slug, to_slug, description = link_data
    
    # Handle both page objects and slugs
    if isinstance(from_slug, str):
        from_page = get_page_by_slug(from_slug)
    else:
        from_page = from_slug
        
    if isinstance(to_slug, str):
        to_page = get_page_by_slug(to_slug)
    else:
        to_page = to_slug
    
    if from_page and to_page:
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
print("COD PANEL DOCUMENTATION COMPLETE")
print("=" * 80)

# Count statistics
total_categories = DocumentationCategory.objects.count()
total_pages = DocumentationPage.objects.count()
total_sections = DocumentationSection.objects.count()
total_tags = DocumentationTag.objects.count()
total_code_examples = CodeExample.objects.count()

print(f"""
📊 DOCUMENTATION STATISTICS:
   Total Categories:      {total_categories}
   Total Pages:           {total_pages}
   Total Sections:        {total_sections}
   Total Tags:            {total_tags}
   Total Code Examples:   {total_code_examples}

📚 NEW COD PANEL CONTENT:
   1. COD Panel Overview - Complete guide to COD Panel
   2. Course Allocation Management - Creating and managing course allocations
   3. Lecturer Management - Managing lecturer profiles and assignments
   4. Program and Course Management - Academic program structure
   5. Lab Allocation Management - Laboratory session scheduling
   6. Submission Workflow Management - Course submission workflow

🔧 KEY FEATURES DOCUMENTED:
   • Department detection and context management
   • Course allocation with group handling
   • Lecturer profile management with user account integration
   • Program and course structure management
   • Laboratory allocation and scheduling
   • Submission workflow with DVC and Timetable Department
   • Rejected course management service
   • Submission control system

💻 TECHNICAL IMPLEMENTATION:
   • COD Panel main view with AJAX support
   • Course allocation creation with validation
   • Utility functions for course code processing
   • Submission control toggle functions
   • Rejected course service class
   • Department detection algorithms

🔗 INTEGRATION POINTS:
   • Links to auto-scheduling system
   • Integration with timetable generation
   • Connection to user roles and permissions
   • Cross-references between COD functions

🚀 ACCESS COD DOCUMENTATION:
   Main COD Panel:      /documentation/page/cod-panel-overview/
   Course Allocation:   /documentation/page/course-allocation-management-guide/
   Lecturer Management: /documentation/page/lecturer-management-guide/
   Program Management:  /documentation/page/program-course-management-guide/
   Lab Allocation:      /documentation/page/lab-allocation-management-guide/
   Submission Workflow: /documentation/page/submission-workflow-management-guide/

🎯 NEXT STEPS FOR CODs:
   1. Review COD Panel overview
   2. Set up lecturer profiles
   3. Create program and course structure
   4. Allocate courses to lecturers
   5. Schedule laboratory sessions
   6. Submit allocations for approval
   7. Monitor submission status

✅ COD Panel documentation successfully created!
""")

print("=" * 80)
print("Documentation now includes comprehensive guides for:")
print("• COD Panel interface and navigation")
print("• Course allocation management with group handling")
print("• Lecturer profile management and user integration")
print("• Academic program and course structure")
print("• Laboratory allocation and scheduling")
print("• Submission workflow management")
print("=" * 80)