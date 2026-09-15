#!/usr/bin/env python
"""
UNIVERSITY TIMETABLING SYSTEM - DOCUMENTATION EXTENSION SCRIPT
This script adds additional documentation pages, updates existing ones, and provides support
for newly created features like autoscheduler, PDF exports, and enhanced functionality.
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
    CodeExample, DocumentationTag, PageTag, InternalLink,
    DocumentationImage, DocumentationAttachment
)
from django.contrib.auth.models import User
from django.utils import timezone
import json

print("=" * 80)
print("UNIVERSITY TIMETABLING SYSTEM - DOCUMENTATION EXTENSION")
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
# 1. CHECK AND UPDATE EXISTING CATEGORIES
# ============================================================

print("\n1. CHECKING AND UPDATING EXISTING CATEGORIES...")

# List of required categories with enhanced descriptions
required_categories = [
    {
        'name': 'Auto-Scheduling System',
        'slug': 'auto-scheduling-system',
        'description': 'Advanced automatic scheduling algorithms with collision prevention and optimization',
        'icon': 'fas fa-robot',
        'order': 11,
        'access_level': 'management',
    },
    {
        'name': 'Export & Reporting',
        'slug': 'export-reporting',
        'description': 'Exporting timetables to PDF, CSV, Excel formats and generating reports',
        'icon': 'fas fa-file-export',
        'order': 12,
        'access_level': 'all',
    },
    {
        'name': 'Lab & Practical Scheduling',
        'slug': 'lab-practical-scheduling',
        'description': 'Scheduling laboratory sessions, practicals, and specialized equipment',
        'icon': 'fas fa-flask',
        'order': 13,
        'access_level': 'management',
    },
    {
        'name': 'Merged Course Management',
        'slug': 'merged-course-management',
        'description': 'Managing merged courses, shared venues, and combined exam scheduling',
        'icon': 'fas fa-object-group',
        'order': 14,
        'access_level': 'department',
    },
    {
        'name': 'Advanced Configuration',
        'slug': 'advanced-configuration',
        'description': 'Advanced system configuration, settings, and customization',
        'icon': 'fas fa-sliders-h',
        'order': 15,
        'access_level': 'admin',
    },
]

categories = {}
for cat_data in required_categories:
    category, created = DocumentationCategory.objects.get_or_create(
        slug=cat_data['slug'],
        defaults=cat_data
    )
    
    # Update if exists but different
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
# 2. ADD NEW TAGS
# ============================================================

print("\n2. ADDING NEW DOCUMENTATION TAGS...")

new_tags = [
    {'name': 'Autoscheduler', 'slug': 'autoscheduler', 'color': '#17a2b8'},
    {'name': 'PDF Export', 'slug': 'pdf-export', 'color': '#dc3545'},
    {'name': 'CSV Export', 'slug': 'csv-export', 'color': '#28a745'},
    {'name': 'Lab Scheduling', 'slug': 'lab-scheduling', 'color': '#6c757d'},
    {'name': 'Merged Courses', 'slug': 'merged-courses', 'color': '#6610f2'},
    {'name': 'Optimization', 'slug': 'optimization', 'color': '#ffc107'},
    {'name': 'Collision Prevention', 'slug': 'collision-prevention', 'color': '#fd7e14'},
    {'name': 'Batch Processing', 'slug': 'batch-processing', 'color': '#20c997'},
]

tags = {}
for tag_data in new_tags:
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
# 3. CREATE NEW DOCUMENTATION PAGES
# ============================================================

print("\n3. CREATING NEW DOCUMENTATION PAGES...")

# Page 1: Auto-Scheduling System
autoscheduler_content = """
<h2>Auto-Scheduling System</h2>
<p>The auto-scheduling system is an advanced algorithm that automatically generates optimal timetables while preventing conflicts and optimizing resource utilization.</p>

<h3>Key Features</h3>
<ul>
<li><strong>Intelligent Conflict Prevention</strong>: Detects and prevents lecturer, program, and venue conflicts</li>
<li><strong>Faculty-First Scheduling</strong>: Prioritizes faculty-specific venues before using general venues</li>
<li><strong>Course Level Awareness</strong>: Differentiates between undergraduate, postgraduate, and PhD courses</li>
<li><strong>Optimized Resource Allocation</strong>: Matches class sizes with appropriate venue capacities</li>
<li><strong>Batch Processing</strong>: Processes courses in optimized batches for efficiency</li>
<li><strong>Real-time Progress Tracking</strong>: Monitors scheduling progress with detailed logs</li>
</ul>

<h3>Architecture</h3>
<p>The auto-scheduler consists of several key components:</p>
<ol>
<li><strong>SchedulerConfig</strong>: Configuration for time slots, days, and scheduling parameters</li>
<li><strong>ConflictTracker</strong>: Tracks and prevents scheduling conflicts</li>
<li><strong>VenueAllocator</strong>: Intelligent venue assignment based on capacity and availability</li>
<li><strong>CourseProcessor</strong>: Handles course prioritization and distribution</li>
<li><strong>BatchProcessor</strong>: Processes courses in optimized batches</li>
</ol>

<h3>Prioritization Logic</h3>
<p>Courses are scheduled based on priority factors:</p>
<ol>
<li>PhD and postgraduate courses (highest priority)</li>
<li>Higher academic years (Year 4 before Year 1)</li>
<li>Professor/Dr designation lecturers</li>
<li>Larger class sizes</li>
<li>Even distribution across days and time slots</li>
</ol>

<h3>Time Slot Management</h3>
<p>The system respects specific time constraints:</p>
<ul>
<li>Postgraduate/PhD courses: Strictly scheduled in afternoon slots</li>
<li>Undergraduate courses: Can use any slot except reserved postgraduate slots</li>
<li>Flexible slot allocation based on course requirements</li>
</ul>

<h3>Batch Processing Strategy</h3>
<p>The system processes courses in intelligent batches:</p>
<ol>
<li><strong>Phase 1 - Faculty Venue Allocation</strong>: Attempts to schedule courses in their faculty's venues</li>
<li><strong>Phase 2 - Comprehensive Fallback</strong>: Uses all available venues for remaining courses</li>
<li><strong>Phase 3 - Final Exhaustive Search</strong>: Attempts every possible combination for stubborn courses</li>
</ol>

<h3>Collision Prevention</h3>
<p>The system implements multiple layers of collision prevention:</p>
<ul>
<li><strong>Lecturer Conflicts</strong>: Prevents same lecturer being scheduled simultaneously</li>
<li><strong>Program Conflicts</strong>: Avoids scheduling same program/year courses at same time</li>
<li><strong>Venue Conflicts</strong>: Prevents double-booking of venues</li>
<li><strong>Day Distribution</strong>: Ensures even distribution of courses across days</li>
</ul>

<h3>Usage</h3>
<p>The auto-scheduler can be accessed via:</p>
<ol>
<li>Admin panel → Auto-Scheduler section</li>
<li>Direct URL: /tt_app/autoscheduler/</li>
<li>API endpoints for programmatic access</li>
</ol>

<h3>Monitoring and Logs</h3>
<p>The system provides comprehensive logging:</p>
<ul>
<li>Real-time progress updates</li>
<li>Detailed console output</li>
<li>Collision detection and resolution logs</li>
<li>Scheduled and unscheduled course lists</li>
</ul>
"""

autoscheduler_page = DocumentationPage.objects.create(
    title='Auto-Scheduling System Guide',
    slug='auto-scheduling-system-guide',
    short_description='Complete guide to the automatic timetable generation system',
    content=autoscheduler_content,
    category=categories['auto-scheduling-system'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=25,
    version='2.0'
)
print(f"   Created: {autoscheduler_page.title}")

# Page 2: PDF Export Guide
pdf_export_content = """
<h2>PDF Export System</h2>
<p>The system provides comprehensive PDF export capabilities for timetables, exam schedules, and reports.</p>

<h3>Export Types</h3>
<h4>1. Main Timetable PDF</h4>
<p>Exports the published teaching timetable including:</p>
<ul>
<li>Regular course schedules</li>
<li>Published merged exam groups</li>
<li>Laboratory sessions</li>
<li>Venue allocations</li>
<li>Time slot breakdown</li>
</ul>

<h4>2. Exam Timetable PDF</h4>
<p>Exports unified examination timetable including:</p>
<ul>
<li>Main exam schedules</li>
<li>Lab exam sessions</li>
<li>Published merged courses</li>
<li>Date and time coordination</li>
<li>Venue allocations</li>
</ul>

<h3>PDF Generation Features</h3>
<ul>
<li><strong>Unified Format</strong>: Combines multiple data sources into single PDF</li>
<li><strong>Deduplication</strong>: Removes duplicate entries automatically</li>
<li><strong>Date Collision Resolution</strong>: Handles date/day conflicts intelligently</li>
<li><strong>Professional Layout</strong>: Clean, readable format with institutional branding</li>
<li><strong>Multi-section Organization</strong>: Separates regular, merged, and lab schedules</li>
</ul>

<h3>Access Methods</h3>
<h4>Web Interface</h4>
<ol>
<li>Navigate to Timetable section</li>
<li>Click "Export to PDF" button</li>
<li>Select export type (Main/Exam)</li>
<li>Generate and download PDF</li>
</ol>

<h4>API Endpoints</h4>
<ul>
<li><code>/tt_app/export/main/pdf/</code> - Main timetable PDF</li>
<li><code>/tt_app/export/exam/pdf/</code> - Exam timetable PDF</li>
</ul>

<h3>Configuration Options</h3>
<h4>Template Customization</h4>
<p>The PDF templates can be customized:</p>
<ul>
<li>Institutional logo replacement</li>
<li>Color scheme adjustments</li>
<li>Header/footer customization</li>
<li>Font and layout modifications</li>
</ul>

<h4>Content Filtering</h4>
<p>Export filters include:</p>
<ul>
<li>Date range filtering</li>
<li>Faculty/department filtering</li>
<li>Venue type filtering</li>
<li>Course level filtering</li>
</ul>

<h3>Performance Considerations</h3>
<ul>
<li><strong>Caching</strong>: Generated PDFs are cached for performance</li>
<li><strong>Batch Processing</strong>: Large exports processed in batches</li>
<li><strong>Memory Management</strong>: Optimized for large datasets</li>
<li><strong>Async Generation</strong>: Background generation for large exports</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Regular Exports</strong>: Export timetables after major changes</li>
<li><strong>Version Control</strong>: Keep multiple versions for reference</li>
<li><strong>Quality Check</strong>: Verify exported data matches system data</li>
<li><strong>Storage Management</strong>: Archive old exports regularly</li>
</ol>
"""

pdf_export_page = DocumentationPage.objects.create(
    title='PDF Export Guide',
    slug='pdf-export-guide',
    short_description='Complete guide to exporting timetables and reports to PDF format',
    content=pdf_export_content,
    category=categories['export-reporting'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='all',
    estimated_read_time=20,
    version='1.5'
)
print(f"   Created: {pdf_export_page.title}")

# Page 3: CSV Export Guide
csv_export_content = """
<h2>CSV Export System</h2>
<p>The CSV export system provides machine-readable data exports for external systems, analysis, and backup purposes.</p>

<h3>Export Types</h3>
<h4>1. Main Timetable CSV</h4>
<p>Exports regular timetable data including:</p>
<ul>
<li>Course codes and names</li>
<li>Lecturer assignments</li>
<li>Venue allocations</li>
<li>Time slots and days</li>
<li>Published merged groups</li>
</ul>

<h4>2. Exam Timetable CSV</h4>
<p>Exports examination schedule data including:</p>
<ul>
<li>Exam course details</li>
<li>Date and time allocations</li>
<li>Venue assignments</li>
<li>Published merged courses</li>
<li>Lab exam sessions</li>
</ul>

<h3>CSV Format Details</h3>
<h4>Column Structure</h4>
<p>Standard CSV format includes:</p>
<ul>
<li><strong>Course Code</strong>: Unique course identifier</li>
<li><strong>Course Name</strong>: Full course name</li>
<li><strong>Lecturer</strong>: Assigned lecturer name</li>
<li><strong>Venue</strong>: Scheduled venue code</li>
<li><strong>Day</strong>: Day of the week</li>
<li><strong>Date</strong>: Specific date (for exams)</li>
<li><strong>Start Time</strong>: Session start time</li>
<li><strong>End Time</strong>: Session end time</li>
<li><strong>Type</strong>: Entry type (Regular/Merged/Lab)</li>
</ul>

<h4>Data Consistency</h4>
<p>The export ensures:</p>
<ul>
<li>No duplicate entries</li>
<li>Consistent date formatting</li>
<li>Standardized time formats</li>
<li>UTF-8 encoding for special characters</li>
<li>Proper escaping of CSV special characters</li>
</ul>

<h3>Access Methods</h3>
<h4>Web Interface</h4>
<ol>
<li>Navigate to Export section</li>
<li>Select "Export to CSV"</li>
<li>Choose timetable type</li>
<li>Download generated CSV</li>
</ol>

<h4>API Endpoints</h4>
<ul>
<li><code>/tt_app/export/main/csv/</code> - Main timetable CSV</li>
<li><code>/tt_app/export/exam/csv/</code> - Exam timetable CSV</li>
</ul>

<h4>Programmatic Access</h4>
<p>CSV exports can be accessed programmatically for automation:</p>

<h3>Integration Options</h3>
<h4>External System Integration</h4>
<p>CSV exports can be imported into:</p>
<ul>
<li>Microsoft Excel/Google Sheets</li>
<li>Database systems</li>
<li>Academic management systems</li>
<li>Calendar applications</li>
<li>Reporting tools</li>
</ul>

<h4>Automation Scenarios</h4>
<ol>
<li><strong>Scheduled Exports</strong>: Automate daily/weekly exports</li>
<li><strong>Data Synchronization</strong>: Sync with external systems</li>
<li><strong>Backup Creation</strong>: Automated data backups</li>
<li><strong>Report Generation</strong>: Feed data into reporting tools</li>
</ol>

<h3>Advanced Features</h3>
<h4>Filtered Exports</h4>
<p>Export specific data subsets:</p>
<ul>
<li>By department/faculty</li>
<li>By date range</li>
<li>By course level</li>
<li>By venue type</li>
</ul>

<h4>Custom Formatting</h4>
<p>Customize CSV output:</p>
<ul>
<li>Column selection</li>
<li>Date/time formatting</li>
<li>Delimiter selection</li>
<li>Header customization</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Regular Backups</strong>: Export data regularly for backup</li>
<li><strong>Data Validation</strong>: Verify exported data integrity</li>
<li><strong>Version Control</strong>: Keep historical export versions</li>
<li><strong>Storage Optimization</strong>: Compress old exports</li>
<li><strong>Security</strong>: Secure sensitive exported data</li>
</ol>
"""

csv_export_page = DocumentationPage.objects.create(
    title='CSV Export Guide',
    slug='csv-export-guide',
    short_description='Complete guide to exporting data to CSV format for external systems',
    content=csv_export_content,
    category=categories['export-reporting'],
    page_type='guide',
    difficulty='intermediate',
    order=2,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='all',
    estimated_read_time=15,
    version='1.3'
)
print(f"   Created: {csv_export_page.title}")

# Page 4: Lab Scheduling Guide
lab_scheduling_content = """
<h2>Lab and Practical Scheduling System</h2>
<p>Specialized scheduling system for laboratory sessions, practicals, and equipment-based courses.</p>

<h3>Key Features</h3>
<ul>
<li><strong>Lab-Specific Venues</strong>: Separate from regular lecture venues</li>
<li><strong>Equipment Tracking</strong>: Track lab equipment and resources</li>
<li><strong>Capacity Management</strong>: Lab-specific capacity constraints</li>
<li><strong>Special Requirements</strong>: Handle specialized lab requirements</li>
<li><strong>Safety Considerations</strong>: Account for safety regulations</li>
</ul>

<h3>Lab Models</h3>
<h4>LabVenue Model</h4>
<p>Represents laboratory venues with specialized attributes:</p>
<ul>
<li><strong>Code</strong>: Unique lab identifier (e.g., CS_LAB_1)</li>
<li><strong>Capacity</strong>: Student capacity with safety limits</li>
<li><strong>Equipment</strong>: List of available equipment</li>
<li><strong>Description</strong>: Special requirements and notes</li>
</ul>

<h4>LabAllocation Model</h4>
<p>Allocates program courses to lab venues:</p>
<ul>
<li><strong>Program Course</strong>: Course requiring lab sessions</li>
<li><strong>Lab Venue</strong>: Assigned laboratory</li>
<li><strong>Lecturer</strong>: Lab instructor</li>
<li><strong>Student Count</strong>: Number of students in lab</li>
</ul>

<h4>LabTimetable Model</h4>
<p>Scheduled lab sessions:</p>
<ul>
<li><strong>Lab Allocation</strong>: Reference to lab allocation</li>
<li><strong>Lab Venue</strong>: Scheduled lab room</li>
<li><strong>Day & Time</strong>: Scheduled session time</li>
<li><strong>Duration</strong>: Lab session length</li>
</ul>

<h4>LabExamTimetable Model</h4>
<p>Scheduled lab examinations:</p>
<ul>
<li><strong>Lab Allocation</strong>: Exam lab allocation</li>
<li><strong>Lab Venue</strong>: Exam lab room</li>
<li><strong>Date & Time</strong>: Exam date and time</li>
<li><strong>Duration</strong>: Exam duration</li>
</ul>

<h3>Scheduling Workflow</h3>
<ol>
<li><strong>Lab Allocation Creation</strong>: Assign courses to lab venues</li>
<li><strong>Resource Verification</strong>: Check equipment and capacity</li>
<li><strong>Schedule Generation</strong>: Generate lab timetable</li>
<li><strong>Conflict Resolution</strong>: Resolve lab-specific conflicts</li>
<li><strong>Publication</strong>: Publish lab schedule</li>
</ol>

<h3>Conflict Prevention</h3>
<p>Lab-specific conflict considerations:</p>
<ul>
<li><strong>Equipment Conflicts</strong>: Avoid double-booking specialized equipment</li>
<li><strong>Setup Time</strong>: Account for lab setup/cleanup time</li>
<li><strong>Safety Limits</strong>: Respect maximum capacity for safety</li>
<li><strong>Concurrent Sessions</strong>: Limit simultaneous sessions per lab</li>
</ul>

<h3>Integration with Main Timetable</h3>
<p>Lab schedules integrate with main timetable:</p>
<ul>
<li>Coordinated time slots</li>
<li>Shared lecturer constraints</li>
<li>Unified export formats</li>
<li>Consistent reporting</li>
</ul>

<h3>Management Interface</h3>
<h4>Admin Panel Features</h4>
<ul>
<li>Lab venue management</li>
<li>Equipment tracking</li>
<li>Capacity monitoring</li>
<li>Schedule visualization</li>
</ul>

<h4>User Access</h4>
<ul>
<li>Lab technicians: View equipment schedules</li>
<li>Lecturers: View their lab sessions</li>
<li>Students: View lab timetables</li>
<li>Administrators: Full management access</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Regular Maintenance</strong>: Schedule lab maintenance time</li>
<li><strong>Equipment Checks</strong>: Regular equipment availability checks</li>
<li><strong>Safety Audits</strong>: Regular safety capacity reviews</li>
<li><strong>Backup Planning</strong>: Plan for equipment failures</li>
<li><strong>Training Scheduling</strong>: Schedule lab technician training</li>
</ol>
"""

lab_scheduling_page = DocumentationPage.objects.create(
    title='Lab and Practical Scheduling Guide',
    slug='lab-scheduling-guide',
    short_description='Complete guide to scheduling laboratory sessions and practicals',
    content=lab_scheduling_content,
    category=categories['lab-practical-scheduling'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=18,
    version='1.8'
)
print(f"   Created: {lab_scheduling_page.title}")

# Page 5: Merged Course Management
merged_courses_content = """
<h2>Merged Course Management System</h2>
<p>Advanced system for managing merged courses, shared venues, and combined exam scheduling.</p>

<h3>Merged Course Types</h3>
<h4>1. AutoMergedExamGroup</h4>
<p>Automatically merged courses during exam scheduling:</p>
<ul>
<li><strong>Automatic Detection</strong>: Detects similar courses for merging</li>
<li><strong>Base Course</strong>: Representative course for the group</li>
<li><strong>Merged Courses</strong>: All courses in the merged group</li>
<li><strong>Total Students</strong>: Sum of all students in group</li>
<li><strong>Published Flag</strong>: Controls group publication status</li>
</ul>

<h4>2. MergedCourseGroup</h4>
<p>Manually created merged course groups:</p>
<ul>
<li><strong>Manual Creation</strong>: Administratively created groups</li>
<li><strong>Base Course</strong>: Primary course representation</li>
<li><strong>Grouped Courses</strong>: Manually selected courses</li>
<li><strong>Venue Sharing</strong>: Shared venue allocation</li>
<li><strong>Published Control</strong>: Manual publication control</li>
</ul>

<h4>3. SharedVenueExamGroup</h4>
<p>Courses sharing venues during exams:</p>
<ul>
<li><strong>Venue Sharing</strong>: Multiple courses in same venue</li>
<li><strong>Time Coordination</strong>: Same date/time for all courses</li>
<li><strong>Capacity Management</strong>: Total student capacity tracking</li>
<li><strong>Conflict Prevention</strong>: Prevents double-booking</li>
</ul>

<h3>Merging Logic</h3>
<h4>Automatic Merging Criteria</h4>
<p>Courses are automatically merged based on:</p>
<ol>
<li><strong>Course Code Similarity</strong>: Same base code (e.g., COSC312, COSC312(A))</li>
<li><strong>Program Alignment</strong>: Same or related programs</li>
<li><strong>Academic Level</strong>: Same academic year/semester</li>
<li><strong>Student Count</strong>: Combined student count fits venue capacity</li>
</ol>

<h4>Manual Merging Process</h4>
<ol>
<li>Select base course for the group</li>
<li>Add courses to merge with base course</li>
<li>Set merged code representation</li>
<li>Configure venue and timing</li>
<li>Publish when ready</li>
</ol>

<h3>Benefits of Course Merging</h3>
<ul>
<li><strong>Resource Optimization</strong>: Better venue utilization</li>
<li><strong>Reduced Conflicts</strong>: Fewer scheduling conflicts</li>
<li><strong>Efficient Examination</strong>: Combined exams save time</li>
<li><strong>Simplified Management</strong>: Fewer individual entries to manage</li>
<li><strong>Cost Savings</strong>: Reduced venue and invigilation costs</li>
</ul>

<h3>Workflow Integration</h3>
<h4>Auto-Scheduler Integration</h4>
<p>Merged groups are considered during auto-scheduling:</p>
<ul>
<li>Grouped courses scheduled together</li>
<li>Appropriate venue selection for group size</li>
<li>Conflict checking for entire group</li>
<li>Publication status respected</li>
</ul>

<h4>Export Integration</h4>
<p>Merged groups in exports:</p>
<ul>
<li>PDF exports show merged groups</li>
<li>CSV exports include group members</li>
<li>Reports aggregate group data</li>
<li>Statistics include merged courses</li>
</ul>

<h3>Management Interface</h3>
<h4>Admin Panel Features</h4>
<ul>
<li>View all merged groups</li>
<li>Create/edit merged groups</li>
<li>Publication control</li>
<li>Group statistics</li>
</ul>

<h4>API Endpoints</h4>
<ul>
<li><code>/tt_app/merged/groups/</code> - List merged groups</li>
<li><code>/tt_app/merged/groups/create/</code> - Create merged group</li>
<li><code>/tt_app/merged/groups/&lt;id&gt;/publish/</code> - Publish group</li>
</ul>

<h3>Best Practices</h3>
<ol>
<li><strong>Strategic Merging</strong>: Merge logically related courses</li>
<li><strong>Capacity Planning</strong>: Ensure venue fits merged group</li>
<li><strong>Communication</strong>: Inform affected departments</li>
<li><strong>Quality Control</strong>: Review merged groups before publication</li>
<li><strong>Documentation</strong>: Document merging rationale</li>
</ol>
"""

merged_courses_page = DocumentationPage.objects.create(
    title='Merged Course Management Guide',
    slug='merged-course-management-guide',
    short_description='Complete guide to managing merged courses and shared venues',
    content=merged_courses_content,
    category=categories['merged-course-management'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='department',
    estimated_read_time=20,
    version='2.0'
)
print(f"   Created: {merged_courses_page.title}")

# Page 6: Advanced Configuration Guide
advanced_config_content = """
<h2>Advanced System Configuration Guide</h2>
<p>Comprehensive guide to advanced system configuration, settings, and customization options.</p>

<h3>Configuration Models</h3>
<h4>SchedulerConfig</h4>
<p>Main scheduling configuration:</p>
<ul>
<li><strong>Start Time</strong>: Earliest scheduling time (default: 07:00)</li>
<li><strong>End Time</strong>: Latest scheduling time (default: 19:00)</li>
<li><strong>Slot Size</strong>: Time slot duration in hours (default: 3)</li>
<li><strong>Days</strong>: Available days for scheduling</li>
</ul>

<h4>ExamSchedulerConfig</h4>
<p>Exam-specific scheduling configuration:</p>
<ul>
<li><strong>Start Date</strong>: Exam period start date</li>
<li><strong>Start/End Times</strong>: Exam session times</li>
<li><strong>Slot Size</strong>: Exam duration in hours</li>
<li><strong>Excluded Days</strong>: Dates to skip (holidays, weekends)</li>
<li><strong>Max Exam Days</strong>: Maximum exam period length</li>
<li><strong>Spacing Ratio</strong>: Venue capacity utilization ratio</li>
</ul>

<h4>LabSchedulerConfig</h4>
<p>Lab scheduling configuration:</p>
<ul>
<li><strong>Start/End Times</strong>: Lab session times</li>
<li><strong>Slot Size</strong>: Lab session duration</li>
</ul>

<h3>System Settings</h3>
<h4>Role and Permission Configuration</h4>
<p>Automatic role creation via signals:</p>

<h4>Audit Log Configuration</h4>
<p>Audit logging settings:</p>
<ul>
<li><strong>Log Level</strong>: Detail level for audit logs</li>
<li><strong>Retention Period</strong>: How long to keep logs</li>
<li><strong>Excluded Models</strong>: Models not to log</li>
<li><strong>User Tracking</strong>: Thread-safe user tracking</li>
</ul>

<h4>Email Configuration</h4>
<p>Notification and alert settings:</p>
<ul>
<li><strong>SMTP Settings</strong>: Email server configuration</li>
<li><strong>Notification Templates</strong>: Email template customization</li>
<li><strong>Alert Triggers</strong>: When to send notifications</li>
<li><strong>Recipient Lists</strong>: Who receives notifications</li>
</ul>

<h3>Performance Configuration</h3>
<h4>Database Optimization</h4>
<ul>
<li><strong>Connection Pooling</strong>: Database connection settings</li>
<li><strong>Query Caching</strong>: Cache frequently used queries</li>
<li><strong>Index Optimization</strong>: Database index configuration</li>
<li><strong>Batch Size</strong>: Bulk operation batch sizes</li>
</ul>

<h4>Memory Management</h4>
<ul>
<li><strong>Cache Settings</strong>: Memory cache configuration</li>
<li><strong>Session Storage</strong>: Session storage optimization</li>
<li><strong>File Upload Limits</strong>: Upload size restrictions</li>
<li><strong>Background Processing</strong>: Async task configuration</li>
</ul>

<h3>Security Configuration</h3>
<h4>Access Control</h4>
<ul>
<li><strong>IP Restrictions</strong>: IP-based access control</li>
<li><strong>Rate Limiting</strong>: API rate limiting</li>
<li><strong>Session Security</strong>: Session timeout and security</li>
<li><strong>Password Policies</strong>: User password requirements</li>
</ul>

<h4>Data Protection</h4>
<ul>
<li><strong>Backup Configuration</strong>: Automated backup settings</li>
<li><strong>Encryption Settings</strong>: Data encryption configuration</li>
<li><strong>Data Retention</strong>: Data retention policies</li>
<li><strong>Export Security</strong>: Secure export settings</li>
</ul>

<h3>Customization Options</h3>
<h4>Template Customization</h4>
<p>Customize system appearance:</p>
<ul>
<li><strong>Theme Settings</strong>: Color schemes and themes</li>
<li><strong>Logo Configuration</strong>: Institutional branding</li>
<li><strong>Layout Customization</strong>: Page layout adjustments</li>
<li><strong>Language Settings</strong>: Multi-language support</li>
</ul>

<h4>Workflow Customization</h4>
<p>Customize business workflows:</p>
<ul>
<li><strong>Approval Chains</strong>: Custom approval workflows</li>
<li><strong>Notification Rules</strong>: Custom notification triggers</li>
<li><strong>Submission Controls</strong>: Workflow control settings</li>
<li><strong>Validation Rules</strong>: Custom data validation</li>
</ul>

<h3>Monitoring and Maintenance</h3>
<h4>System Monitoring</h4>
<ul>
<li><strong>Health Checks</strong>: System health monitoring</li>
<li><strong>Performance Metrics</strong>: Performance monitoring</li>
<li><strong>Error Tracking</strong>: Error logging and tracking</li>
<li><strong>Usage Statistics</strong>: System usage analytics</li>
</ul>

<h4>Maintenance Procedures</h4>
<ol>
<li><strong>Regular Backups</strong>: Scheduled backup procedures</li>
<li><strong>Database Maintenance</strong>: Regular database optimization</li>
<li><strong>Cache Clearing</strong>: Cache management procedures</li>
<li><strong>Log Rotation</strong>: Log file management</li>
<li><strong>Update Procedures</strong>: System update processes</li>
</ol>

<h3>Troubleshooting Configuration</h3>
<h4>Common Issues</h4>
<ul>
<li><strong>Configuration Errors</strong>: Common config mistakes</li>
<li><strong>Performance Issues</strong>: Performance tuning</li>
<li><strong>Security Issues</strong>: Security configuration problems</li>
<li><strong>Integration Issues</strong>: External system integration</li>
</ul>

<h4>Debug Tools</h4>
<ul>
<li><strong>Configuration Validation</strong>: Config validation tools</li>
<li><strong>Performance Profiling</strong>: Performance analysis tools</li>
<li><strong>Security Auditing</strong>: Security audit tools</li>
<li><strong>Log Analysis</strong>: Log analysis utilities</li>
</ul>
"""

advanced_config_page = DocumentationPage.objects.create(
    title='Advanced Configuration Guide',
    slug='advanced-configuration-guide',
    short_description='Complete guide to advanced system configuration and customization',
    content=advanced_config_content,
    category=categories['advanced-configuration'],
    page_type='guide',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=30,
    version='3.0'
)
print(f"   Created: {advanced_config_page.title}")

# ============================================================
# 4. CREATE SECTIONS WITH DETAILED CODE EXAMPLES
# ============================================================

print("\n4. CREATING SECTIONS WITH DETAILED CODE EXAMPLES...")

# Section 1: Auto-Scheduler Implementation Details
autoscheduler_section = DocumentationSection.objects.create(
    page=autoscheduler_page,
    title='Implementation Details and Code Architecture',
    content='''
<h3>Core Auto-Scheduler Components</h3>
<p>The auto-scheduler is implemented as a sophisticated Python module with several key components working together.</p>
''',
    order=1,
    is_active=True,
    slug='autoscheduler-implementation-details'
)
print(f"   Created section: {autoscheduler_section.title}")

# Code Example 1: Auto-Scheduler Main Function
code_example_autoscheduler1 = CodeExample.objects.create(
    section=autoscheduler_section,
    title='Main Auto-Scheduler Thread Function',
    code='''def run_optimized_autoscheduler_thread():
    total_courses = 0
    all_scheduled_courses = []
    all_unscheduled_courses = []
    
    # Initialize caches and trackers
    cache = SchedulerCache()
    conflict_tracker = None
    venue_allocator = None
    
    try:
        with transaction.atomic():
            TempTimetable.objects.all().delete()
            AutoMergedExamGroup.objects.all().delete()

        config = SchedulerConfig.objects.first() or SchedulerConfig.objects.create()
        
        # Get all venues and faculty mapping using cache
        all_venues = cache.get_all_venues()
        faculty_venues_map = cache.get_faculty_venues_map()
        
        # Days and time slots configuration
        days = getattr(config, 'days', None) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        slots = generate_slots(start_time, end_time, slot_size)

        # Initialize enhanced conflict tracker
        conflict_tracker = ConflictTracker(days, slots)
        venue_allocator = VenueAllocator(all_venues, days, slots, conflict_tracker)

        # Get all courses
        all_courses = list(CourseAllocation.objects.select_related(
            'program', 'lecturer', 'department', 
            'program__department', 'program__department__faculty',
            'lecturer__department', 'lecturer__department__faculty'
        ).all())
        
        # ... rest of the scheduling logic ...
        
    except Exception as e:
        error_details = traceback.format_exc()
        error_msg = f"Scheduling failed: {str(e)}"
        print(f"Scheduler error:\\n{error_details}")
        update_progress(0, f"Error: {str(e)}", 0, total_courses if total_courses else 0, 
                      console_message=error_msg,
                      scheduled_courses=all_scheduled_courses,
                      unscheduled_courses=all_unscheduled_courses)
        return {
            'status': 'error', 
            'message': error_msg, 
            'scheduled_count': 0, 
            'remaining_count': total_courses if total_courses else 0,
            'scheduled_courses': all_scheduled_courses,
            'unscheduled_courses': all_unscheduled_courses
        }''',
    language='python',
    order=1,
    description='Main thread function that orchestrates the entire auto-scheduling process'
)

# Code Example 2: Conflict Tracker
code_example_autoscheduler2 = CodeExample.objects.create(
    section=autoscheduler_section,
    title='ConflictTracker Class for Collision Prevention',
    code='''class ConflictTracker:
    """Efficient in-memory conflict tracking with enhanced program-year collision prevention"""
    
    def __init__(self, days, slots):
        self.days = days
        self.slots = slots
        self.lecturer_schedule = defaultdict(lambda: defaultdict(set))
        self.program_schedule = defaultdict(lambda: defaultdict(set))
        self.venue_schedule = defaultdict(lambda: defaultdict(set))
        
        # Enhanced program-year distribution tracking
        self.program_year_daily_count = defaultdict(lambda: defaultdict(int))
        self.program_year_slot_distribution = defaultdict(lambda: defaultdict(set))
        self.program_year_assignments = defaultdict(set)
        
        # Convert time slots to indices for faster comparison
        self.slot_indices = {slot: idx for idx, slot in enumerate(slots)}
        self.slot_overlaps = self._precompute_slot_overlaps()
        
        # Collision resolution statistics
        self.collisions_detected = 0
        self.collisions_resolved = 0
        
    def _precompute_slot_overlaps(self):
        """Properly detect overlaps between ALL slots"""
        overlaps = defaultdict(set)
        
        # Convert all slots to minutes for precise comparison
        slot_minutes = []
        for start, end in self.slots:
            start_min = start.hour * 60 + start.minute
            end_min = end.hour * 60 + end.minute
            slot_minutes.append((start_min, end_min))
        
        for i, (start1_min, end1_min) in enumerate(slot_minutes):
            for j, (start2_min, end2_min) in enumerate(slot_minutes):
                if i == j:
                    continue
                    
                # Check for time overlap
                overlap_condition = (
                    (start1_min < end2_min and end1_min > start2_min)
                )
                
                if overlap_condition:
                    overlaps[i].add(j)
                    overlaps[j].add(i)
        
        return dict(overlaps)
    
    def add_schedule(self, lecturer_id, program_id, year, venue_id, day, slot_index, course_code=None):
        """Add a scheduled item to conflict tracking with validation"""
        if lecturer_id:
            self.lecturer_schedule[lecturer_id][day].add(slot_index)
        
        if program_id and program_id != 0:
            program_year_key = (program_id, year)
            self.program_schedule[program_year_key][day].add(slot_index)
            self.program_year_daily_count[program_year_key][day] += 1
            self.program_year_slot_distribution[program_year_key][day].add(slot_index)
            
            if course_code:
                self.program_year_assignments[program_year_key].add(course_code)
        
        self.venue_schedule[venue_id][day].add(slot_index)''',
    language='python',
    order=2,
    description='Conflict tracking class that prevents scheduling collisions'
)

# Section 2: PDF Export Implementation
pdf_export_section = DocumentationSection.objects.create(
    page=pdf_export_page,
    title='PDF Export Implementation Details',
    content='''
<h3>PDF Generation Architecture</h3>
<p>The PDF export system uses WeasyPrint for HTML to PDF conversion with advanced template rendering.</p>
''',
    order=2,
    is_active=True,
    slug='pdf-export-implementation'
)
print(f"   Created section: {pdf_export_section.title}")

# Code Example 3: PDF Export View
code_example_pdf1 = CodeExample.objects.create(
    section=pdf_export_section,
    title='Main PDF Export View Function',
    code='''def export_main_pdf(request):
    """
    Export the published main teaching timetable to PDF.
    Includes AutoMergedExamGroup rows where published=True.
    """
    logo_url = request.build_absolute_uri(static('chuka.png'))

    # --- Scheduler Config Defaults ---
    config = SchedulerConfig.objects.first()
    start_time = config.start_time if config else datetime.strptime("07:00", "%H:%M").time()
    end_time = config.end_time if config else datetime.strptime("19:00", "%H:%M").time()
    slot_size = config.slot_size if config else 3

    # --- Time Slots ---
    time_slots = []
    current = datetime.combine(datetime.today(), start_time)
    end_dt = datetime.combine(datetime.today(), end_time)
    while current < end_dt:
        next_slot = current + timedelta(hours=slot_size)
        if next_slot > end_dt:
            next_slot = end_dt
        time_slots.append(f"{current.strftime('%H:%M')} - {next_slot.strftime('%H:%M')}")
        current = next_slot

    # --- Querysets ---
    timetable_qs = Timetable.objects.select_related(
        "course_allocation__lecturer"
    ).all().order_by("venue", "start_time", "end_time", "day")

    # ✅ Only published merged groups
    merged_qs = AutoMergedExamGroup.objects.select_related(
        "venue", "base_course__lecturer"
    ).prefetch_related("merged_courses").filter(published=True).order_by("venue", "start_time", "end_time", "date")

    if not timetable_qs.exists() and not merged_qs.exists():
        return HttpResponse("No timetable data found.", content_type="text/plain")

    # --- Context ---
    context = {
        "title": "Teaching Timetable (Published)",
        "logo_url": logo_url,
        "timetable_data": list(timetable_qs),
        "merged_data": list(merged_qs),
        "time_slots": time_slots,
        "now": timezone.now(),
    }

    # --- Render PDF ---
    html_string = render_to_string("timetable_main_pdf.html", context, request=request)
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="timetable.pdf"'
    return response''',
    language='python',
    order=1,
    description='Main view function for PDF export with merged course support'
)

# Code Example 4: HTML Template Structure
code_example_pdf2 = CodeExample.objects.create(
    section=pdf_export_section,
    title='HTML Template Structure for PDF Generation',
    code='''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{{ title }}</title>
    <style>
        @page {
            size: A4 landscape;
            margin: 1cm;
        }
        body {
            font-family: 'Helvetica', 'Arial', sans-serif;
            font-size: 10pt;
        }
        .header {
            text-align: center;
            margin-bottom: 20px;
        }
        .logo {
            height: 80px;
        }
        .title {
            font-size: 16pt;
            font-weight: bold;
            margin: 10px 0;
        }
        .subtitle {
            font-size: 12pt;
            margin-bottom: 20px;
        }
        .timetable-table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }
        .timetable-table th,
        .timetable-table td {
            border: 1px solid #ddd;
            padding: 6px;
            text-align: left;
            vertical-align: top;
        }
        .timetable-table th {
            background-color: #f2f2f2;
            font-weight: bold;
        }
        .venue-header {
            background-color: #e8f4f8;
            font-weight: bold;
        }
        .course-entry {
            margin-bottom: 4px;
            padding: 2px;
            border-bottom: 1px dotted #eee;
        }
        .course-code {
            font-weight: bold;
            color: #0066cc;
        }
        .lecturer {
            font-style: italic;
            color: #666;
            font-size: 9pt;
        }
        .merged-indicator {
            background-color: #fff3cd;
            border-left: 3px solid #ffc107;
            padding-left: 5px;
        }
        .footer {
            margin-top: 30px;
            text-align: center;
            font-size: 9pt;
            color: #666;
            border-top: 1px solid #ddd;
            padding-top: 10px;
        }
    </style>
</head>
<body>
    <div class="header">
        {% if logo_url %}
        <img src="{{ logo_url }}" class="logo" alt="University Logo">
        {% endif %}
        <div class="title">{{ title }}</div>
        <div class="subtitle">Generated on {{ now|date:"F j, Y H:i" }}</div>
    </div>
    
    <!-- Main timetable table -->
    <table class="timetable-table">
        <thead>
            <tr>
                <th>Venue</th>
                {% for slot in time_slots %}
                <th>{{ slot }}</th>
                {% endfor %}
            </tr>
        </thead>
        <tbody>
            {% for venue_data in day_grids %}
            <tr>
                <td class="venue-header">{{ venue_data.venue_key }}</td>
                {% for cell in venue_data.cells %}
                <td>
                    {% for entry in cell.entries %}
                    <div class="course-entry {% if 'Merged' in entry.category %}merged-indicator{% endif %}">
                        <div class="course-code">{{ entry.course_code }}</div>
                        <div class="lecturer">{{ entry.lecturer }}</div>
                        {% if entry.category %}
                        <div class="category" style="font-size: 8pt; color: #888;">{{ entry.category }}</div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </td>
                {% endfor %}
            </tr>
            {% endfor %}
        </tbody>
    </table>
    
    <!-- Merged courses section -->
    {% if merged_table %}
    <h3>Merged Exam Groups</h3>
    <table class="timetable-table">
        <thead>
            <tr>
                <th>Merged Code</th>
                <th>Courses</th>
                <th>Total Students</th>
                <th>Venue</th>
                <th>Date & Time</th>
                <th>Lecturer</th>
            </tr>
        </thead>
        <tbody>
            {% for merged in merged_table %}
            <tr>
                <td>{{ merged.merged_code }}</td>
                <td>{{ merged.courses }}</td>
                <td>{{ merged.total_students }}</td>
                <td>{{ merged.venue }}</td>
                <td>{{ merged.date }} {{ merged.start }}-{{ merged.end }}</td>
                <td>{{ merged.lecturer }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% endif %}
    
    <div class="footer">
        Generated by University Timetabling System • Page {% block page_number %}{% endblock %}
    </div>
</body>
</html>''',
    language='html',
    order=2,
    description='HTML template structure for PDF generation with styling'
)

# Section 3: CSV Export Implementation
csv_export_section = DocumentationSection.objects.create(
    page=csv_export_page,
    title='CSV Export Implementation Details',
    content='''
<h3>CSV Export Architecture</h3>
<p>The CSV export system uses Django's CSV response with optimized data handling and deduplication.</p>
''',
    order=3,
    is_active=True,
    slug='csv-export-implementation'
)
print(f"   Created section: {csv_export_section.title}")

# Code Example 5: CSV Export View
code_example_csv1 = CodeExample.objects.create(
    section=csv_export_section,
    title='Main CSV Export View Function',
    code='''def export_main_csv(request):
    """
    Export unified CSV of normal timetable rows + published auto-merged exam groups.
    Avoids duplicates and preserves all data consistency.
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="timetable.csv"'
    writer = csv.writer(response)

    writer.writerow([
        "Course Code",
        "Course Name",
        "Lecturer",
        "Venue",
        "Day",
        "Start",
        "End",
        "Type"
    ])

    handled_courses = set()

    # -------- NORMAL TIMETABLE ROWS --------
    qs = Timetable.objects.select_related("course_allocation__lecturer", "venue").all()
    for e in qs:
        ca = getattr(e, "course_allocation", None)
        if not ca:
            continue

        course_code = getattr(ca, "course_code", "")
        course_name = getattr(ca, "course_name", "")
        handled_courses.add(course_code)

        # Lecturer
        lecturer = "Unassigned"
        if getattr(ca, "lecturer", None):
            if hasattr(ca.lecturer, "display_name"):
                lecturer = ca.lecturer.display_name
            elif hasattr(ca.lecturer, "get_full_name"):
                lecturer = ca.lecturer.get_full_name()
            elif hasattr(ca.lecturer, "name"):
                lecturer = ca.lecturer.name

        # Venue
        v = getattr(e, "venue", None)
        if v:
            venue_display = getattr(v, "code", getattr(v, "name", str(v)))
        else:
            venue_display = "Unassigned"

        writer.writerow([
            course_code,
            course_name,
            lecturer,
            venue_display,
            e.day or "",
            e.start_time.strftime("%H:%M") if e.start_time else "",
            e.end_time.strftime("%H:%M") if e.end_time else "",
            "Regular Timetable"
        ])

    # -------- PUBLISHED AUTO-MERGED GROUPS --------
    merged_qs = AutoMergedExamGroup.objects.filter(published=True).select_related("base_course", "venue").prefetch_related("merged_courses__lecturer")

    for group in merged_qs:
        base = group.base_course
        if not base:
            continue

        base_code = getattr(base, "course_code", "N/A")
        base_name = getattr(base, "course_name", "N/A")

        # Venue
        v = getattr(group, "venue", None)
        venue_display = getattr(v, "code", getattr(v, "name", str(v))) if v else "Unassigned"

        # Base course lecturer
        lecturer = getattr(base.lecturer, "display_name", "Unassigned") if getattr(base, "lecturer", None) else "Unassigned"

        # Include base row
        writer.writerow([
            base_code,
            base_name,
            lecturer,
            venue_display,
            getattr(group, "date", ""),
            group.start_time.strftime("%H:%M") if group.start_time else "",
            group.end_time.strftime("%H:%M") if group.end_time else "",
            "Merged Exam (Published)"
        ])

        # Include all merged courses
        for mc in group.merged_courses.all():
            mc_code = getattr(mc, "course_code", "N/A")
            mc_name = getattr(mc, "course_name", "N/A")
            if mc_code in handled_courses:
                continue  # avoid duplicates

            mc_lecturer = getattr(mc.lecturer, "display_name", "Unassigned") if getattr(mc, "lecturer", None) else "Unassigned"

            writer.writerow([
                mc_code,
                mc_name,
                mc_lecturer,
                venue_display,
                getattr(group, "date", ""),
                group.start_time.strftime("%H:%M") if group.start_time else "",
                group.end_time.strftime("%H:%M") if group.end_time else "",
                "Merged Exam (Published)"
            ])
            handled_courses.add(mc_code)

    return response''',
    language='python',
    order=1,
    description='CSV export view with deduplication and merged course handling'
)

# Section 4: Lab Scheduling Implementation
lab_scheduling_section = DocumentationSection.objects.create(
    page=lab_scheduling_page,
    title='Lab Scheduling Implementation Details',
    content='''
<h3>Lab Model Architecture</h3>
<p>The lab scheduling system uses specialized models for lab venues, allocations, and timetables.</p>
''',
    order=4,
    is_active=True,
    slug='lab-scheduling-implementation'
)
print(f"   Created section: {lab_scheduling_section.title}")

# Code Example 6: Lab Models
code_example_lab1 = CodeExample.objects.create(
    section=lab_scheduling_section,
    title='Lab Model Definitions',
    code='''from django.db import models

class LabVenue(models.Model):
    """
    Represents a laboratory venue (separate from normal lecture venues).
    """
    code = models.CharField(max_length=50, unique=True)  # e.g. CS LAB 1
    capacity = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    equipment = models.TextField(blank=True, null=True, help_text="List of lab equipment or resources")
    
    def __str__(self):
        cap = self.capacity if self.capacity is not None else "Unknown capacity"
        return f"{self.code} ({cap} seats)"

class LabAllocation(models.Model):
    """
    Allocation of lab sessions to courses.
    """
    program_course = models.ForeignKey(
        "ProgramCourse", on_delete=models.CASCADE, related_name="lab_allocations"
    )
    venue = models.ForeignKey("LabVenue", on_delete=models.CASCADE, related_name="lab_allocations")
    lecturer = models.ForeignKey("Lecturer", on_delete=models.SET_NULL, null=True, blank=True)
    number_of_students = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.program_course.course_code} - {self.venue.code}"

class LabTimetable(models.Model):
    """
    A scheduled lab session for a lab allocation.
    """
    lab_allocation = models.ForeignKey(
        "LabAllocation", on_delete=models.CASCADE, related_name="lab_timetables"
    )
    lab_venue = models.ForeignKey(
        "LabVenue", on_delete=models.CASCADE, related_name="lab_timetable_entries"
    )
    day = models.CharField(max_length=16)  # e.g. Monday
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("lab_venue", "day", "start_time", "end_time")

    def __str__(self):
        return f"{self.lab_allocation.program_course.course_code} - {self.lab_venue.code} {self.day} {self.start_time}-{self.end_time}"

class LabExamTimetable(models.Model):
    """
    Scheduled Lab Exam session.
    """
    lab_allocation = models.ForeignKey(
        "LabAllocation", on_delete=models.CASCADE, related_name="lab_exam_timetables"
    )
    lab_venue = models.ForeignKey(
        LabVenue, on_delete=models.CASCADE, related_name="lab_exam_timetable_entries"
    )
    date = models.DateField(default=timezone.now)
    day = models.CharField(max_length=16)
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("lab_venue", "date", "start_time", "end_time")
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"{self.lab_allocation.program_course.course_code} - {self.lab_venue.code} {self.day} {self.date} {self.start_time}-{self.end_time}"''',
    language='python',
    order=1,
    description='Lab scheduling model definitions'
)

# Section 5: Merged Course Implementation
merged_courses_section = DocumentationSection.objects.create(
    page=merged_courses_page,
    title='Merged Course Implementation Details',
    content='''
<h3>Merged Course Model Architecture</h3>
<p>The merged course system uses specialized models for auto-merged, manually merged, and shared venue groups.</p>
''',
    order=5,
    is_active=True,
    slug='merged-courses-implementation'
)
print(f"   Created section: {merged_courses_section.title}")

# Code Example 7: Merged Course Models
code_example_merged1 = CodeExample.objects.create(
    section=merged_courses_section,
    title='Merged Course Model Definitions',
    code='''from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

class AutoMergedExamGroup(models.Model):
    """
    Represents multiple CourseAllocation rows merged automatically
    during exam auto-scheduling (e.g., COSC312, COSC312(A), COSC312(C)).
    """
    base_course = models.ForeignKey(
        "CourseAllocation",
        on_delete=models.CASCADE,
        related_name="auto_merged_as_base",
        help_text="Representative base course for this auto-merged exam group"
    )
    merged_courses = models.ManyToManyField(
        "CourseAllocation",
        related_name="auto_merged_groups",
        help_text="All course allocations included in this merged exam"
    )
    merged_code = models.CharField(
        max_length=50,
        blank=True,
        help_text="Normalized base course code (e.g., COSC312)"
    )
    total_students = models.PositiveIntegerField(default=0)
    date = models.CharField(max_length=100,blank=True,default="check in timetable")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    venue = models.ForeignKey("Venue", null=True, blank=True, on_delete=models.SET_NULL)

    # published flag (new)
    published = models.BooleanField(default=False, help_text="Has this auto-merged group been published/approved?")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.merged_code} ({self.total_students} students) on {self.date or 'TBD'}"

class MergedCourseGroup(models.Model):
    """
    Records that several CourseAllocation rows were merged for a single exam sitting.
    """
    base_course = models.ForeignKey(
        "CourseAllocation",
        on_delete=models.CASCADE,
        related_name="merged_as_base",
        help_text="Representative course allocation (first code)"
    )
    merged_courses = models.ManyToManyField(
        "CourseAllocation",
        related_name="merged_groups",
        help_text="All course allocations included in this merged exam"
    )
    merged_code = models.CharField(max_length=50, blank=True, help_text="Normalized base code")
    total_students = models.PositiveIntegerField(default=0)
    date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    venue = models.ForeignKey("Venue", null=True, blank=True, on_delete=models.SET_NULL)

    # published flag (new)
    published = models.BooleanField(default=False, help_text="Has this merged group been published/approved?")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"MergedGroup: {self.merged_code} ({self.total_students} students) on {self.date} {self.start_time}"

class SharedVenueExamGroup(models.Model):
    """
    Allows multiple CourseAllocation entries to share one exam venue
    at the same date/time slot while preventing time collisions.
    """
    venue = models.ForeignKey(
        "Venue",
        on_delete=models.CASCADE,
        related_name="shared_exam_groups",
        help_text="Venue being shared for this exam session"
    )

    date = models.DateField(default=timezone.now)
    day = models.CharField(max_length=20, help_text="Day of the exam (e.g., Monday)")
    start_time = models.TimeField()
    end_time = models.TimeField()

    # Courses sharing this venue
    course_allocations = models.ManyToManyField(
        "CourseAllocation",
        related_name="shared_exam_venues",
        help_text="Courses sharing this venue at the same time"
    )

    total_students = models.PositiveIntegerField(default=0, help_text="Total number of students across all shared courses")

    # published flag (new)
    published = models.BooleanField(default=False, help_text="Has this shared group been published/approved?")

    # Optional: system tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("venue", "date", "start_time", "end_time")
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"{self.venue.code} shared on {self.date} {self.start_time}-{self.end_time}"''',
    language='python',
    order=1,
    description='Merged course model definitions'
)

# ============================================================
# 5. ASSIGN TAGS TO NEW PAGES
# ============================================================

print("\n5. ASSIGNING TAGS TO NEW PAGES...")

# Define tag assignments for new pages
new_tag_assignments = {
    autoscheduler_page: ['autoscheduler', 'optimization', 'collision-prevention', 'batch-processing'],
    pdf_export_page: ['pdf-export', 'export-reporting'],
    csv_export_page: ['csv-export', 'export-reporting'],
    lab_scheduling_page: ['lab-scheduling', 'technical'],
    merged_courses_page: ['merged-courses', 'optimization'],
    advanced_config_page: ['technical', 'administration'],
}

for page, tag_slugs in new_tag_assignments.items():
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

# Define new internal links
new_internal_links = [
    # Link new pages to existing ones
    (autoscheduler_page, pdf_export_page, "Export generated timetables to PDF"),
    (autoscheduler_page, csv_export_page, "Export data to CSV for external systems"),
    (pdf_export_page, csv_export_page, "Alternative export format options"),
    
    # Link existing pages to new ones
    ('system-architecture-overview', autoscheduler_page.slug, "Learn about automatic scheduling"),
    ('timetable-generation-guide', autoscheduler_page.slug, "Automatic timetable generation"),
    ('technical-reference-guide', advanced_config_page.slug, "Advanced system configuration"),
    
    # Cross-link new pages
    (lab_scheduling_page, merged_courses_page, "Lab sessions can be merged too"),
    (merged_courses_page, autoscheduler_page, "Auto-scheduler handles merged courses"),
    (advanced_config_page, autoscheduler_page, "Configure auto-scheduler settings"),
]

# Helper function to get page by slug
def get_page_by_slug(slug):
    try:
        return DocumentationPage.objects.get(slug=slug)
    except DocumentationPage.DoesNotExist:
        return None

for link_data in new_internal_links:
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
        InternalLink.objects.get_or_create(
            from_page=from_page,
            to_page=to_page,
            defaults={'description': description}
        )
        print(f"   Linked: {from_page.title} → {to_page.title}")

# ============================================================
# 7. UPDATE EXISTING PAGES WITH NEW INFORMATION
# ============================================================

print("\n7. UPDATING EXISTING PAGES WITH NEW INFORMATION...")

# Get existing pages to update
existing_pages_to_update = {
    'technical-reference-guide': {
        'update_content': True,
        'new_sections': [
            {
                'title': 'Auto-Scheduler Integration',
                'content': '''
<h3>Auto-Scheduler Integration Points</h3>
<p>The auto-scheduler integrates with the main system through several key interfaces:</p>
<ul>
<li><strong>Course Allocation Integration</strong>: Uses CourseAllocation model for scheduling input</li>
<li><strong>Venue Management Integration</strong>: Integrates with Building and Venue models</li>
<li><strong>Conflict Tracking</strong>: Uses enhanced conflict detection algorithms</li>
<li><strong>Progress Monitoring</strong>: Real-time progress tracking via thread-safe variables</li>
<li><strong>Database Integration</strong>: Bulk database operations for performance</li>
</ul>
''',
                'order': 5
            }
        ]
    },
    'timetable-generation-guide': {
        'update_content': True,
        'new_sections': [
            {
                'title': 'Automatic Generation Options',
                'content': '''
<h3>Automatic Timetable Generation</h3>
<p>The system now includes advanced automatic generation options:</p>

<h4>Auto-Scheduler Features</h4>
<ul>
<li><strong>One-Click Generation</strong>: Generate complete timetable with single click</li>
<li><strong>Batch Processing</strong>: Process courses in optimized batches</li>
<li><strong>Conflict Prevention</strong>: Automatic conflict detection and resolution</li>
<li><strong>Resource Optimization</strong>: Efficient venue and time slot utilization</li>
<li><strong>Progress Monitoring</strong>: Real-time progress tracking during generation</li>
</ul>

<h4>Scheduling Strategies</h4>
<ol>
<li><strong>Faculty-First Strategy</strong>: Schedule courses in their faculty venues first</li>
<li><strong>Level-Aware Scheduling</strong>: Differentiate undergraduate/postgraduate courses</li>
<li><strong>Capacity Matching</strong>: Match class sizes with venue capacities</li>
<li><strong>Distribution Optimization</strong>: Even distribution across days and time slots</li>
</ol>

<h4>Export Integration</h4>
<p>Automatically generated timetables can be exported to:</p>
<ul>
<li><strong>PDF Format</strong>: Professional printable format</li>
<li><strong>CSV Format</strong>: Machine-readable data export</li>
<li><strong>Excel Reports</strong>: Detailed analysis reports</li>
<li><strong>Calendar Feeds</strong>: Integration with calendar applications</li>
</ul>
''',
                'order': 3
            }
        ]
    }
}

# Update existing pages
for page_slug, update_info in existing_pages_to_update.items():
    try:
        page = DocumentationPage.objects.get(slug=page_slug)
        
        if update_info.get('update_content'):
            # Add new sections
            for section_info in update_info.get('new_sections', []):
                DocumentationSection.objects.get_or_create(
                    page=page,
                    title=section_info['title'],
                    defaults={
                        'content': section_info['content'],
                        'order': section_info['order'],
                        'is_active': True
                    }
                )
                print(f"   Added section '{section_info['title']}' to: {page.title}")
        
        # Update page metadata if needed
        page.version = f"{float(page.version) + 0.1:.1f}"
        page.save()
        print(f"   Updated version to {page.version} for: {page.title}")
        
    except DocumentationPage.DoesNotExist:
        print(f"   Warning: Page {page_slug} not found for update")

# ============================================================
# 8. CREATE ATTACHMENTS AND ADDITIONAL RESOURCES
# ============================================================

print("\n8. CREATING SAMPLE ATTACHMENTS AND RESOURCES...")

# Create sample attachments (would need actual files in production)
sample_attachments = [
    {
        'page': autoscheduler_page,
        'name': 'Auto-Scheduler Configuration Guide',
        'description': 'Detailed configuration options for the auto-scheduler',
        'file_type': 'pdf',
        'file_size': 1024000,
    },
    {
        'page': pdf_export_page,
        'name': 'PDF Template Customization Guide',
        'description': 'How to customize PDF export templates',
        'file_type': 'pdf',
        'file_size': 512000,
    },
    {
        'page': csv_export_page,
        'name': 'CSV Import/Export Specification',
        'description': 'Complete specification for CSV data formats',
        'file_type': 'pdf',
        'file_size': 256000,
    },
]

# Note: In production, you would add actual files
print("   Sample attachments defined (add actual files in production)")

# ============================================================
# 9. SUMMARY
# ============================================================

print("\n" + "=" * 80)
print("DOCUMENTATION EXTENSION COMPLETE")
print("=" * 80)

# Count statistics
total_categories = DocumentationCategory.objects.count()
total_pages = DocumentationPage.objects.count()
total_sections = DocumentationSection.objects.count()
total_tags = DocumentationTag.objects.count()
total_code_examples = CodeExample.objects.count()
total_links = InternalLink.objects.count()

print(f"""
📊 EXTENDED DOCUMENTATION STATISTICS:
   Total Categories:      {total_categories}
   Total Pages:           {total_pages}
   Total Sections:        {total_sections}
   Total Tags:            {total_tags}
   Total Code Examples:   {total_code_examples}
   Total Internal Links:  {total_links}

📚 NEW CONTENT ADDED:
   1. Auto-Scheduling System Guide
   2. PDF Export Guide
   3. CSV Export Guide
   4. Lab Scheduling Guide
   5. Merged Course Management Guide
   6. Advanced Configuration Guide

🔄 UPDATED CONTENT:
   - Technical Reference Guide (added auto-scheduler integration)
   - Timetable Generation Guide (added automatic generation options)

🔗 NEW INTEGRATION POINTS:
   - Auto-scheduler ↔ PDF Export
   - Auto-scheduler ↔ CSV Export
   - Lab Scheduling ↔ Merged Courses
   - Technical Reference ↔ Advanced Configuration

🚀 ACCESS NEW DOCUMENTATION:
   Main URL:        /documentation/
   Auto-Scheduler:  /documentation/page/auto-scheduling-system-guide/
   PDF Export:      /documentation/page/pdf-export-guide/
   CSV Export:      /documentation/page/csv-export-guide/
   Lab Scheduling:  /documentation/page/lab-scheduling-guide/

🎯 NEXT STEPS:
   1. Review new documentation at /documentation/
   2. Test auto-scheduler functionality
   3. Try PDF and CSV exports
   4. Configure lab scheduling
   5. Set up merged course management
   6. Review advanced configuration options

✅ Documentation successfully extended with new features and updates!
""")

print("=" * 80)
print("Documentation extension complete. System now includes:")
print("• Advanced auto-scheduling with collision prevention")
print("• Comprehensive PDF and CSV export systems")
print("• Lab and practical scheduling capabilities")
print("• Merged course management")
print("• Advanced configuration options")
print("=" * 80)