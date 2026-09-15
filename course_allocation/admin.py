from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.html import format_html
from django.core.cache import cache

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget, BooleanWidget, DateWidget
from import_export.results import RowResult

from .models import (
    CourseAllocation,
    StudentGroup,
    GroupingTemplate,
    GroupingTemplateGroup,
    GroupingTemplateCourse,
    SelectionGroup,
    SpecialIntakeGroup,
    SpecializationCategory,
    SpecializationStem,
    BaseSelection,
    AllocationConfig,
    LecturerCourseMapping,
    ProgramEnrollment,
    AcademicYearTracker,
    LabAllocation,
    SubmissionControl,
    ArchivedCourseAllocation,
    CombinedCourseGroup,
    CourseCombinationTemplate,
    CourseCombinationTemplateProgram,
    DVCActionLog,
)
from department_management.models import Department
from program_management.models import Program, ProgramCourse
from lecturer_portal.models import Lecturer
from room_management.models import LabVenue
from .config_helpers import strip_course_code_tag, normalize_course_code

import logging
import re
logger = logging.getLogger(__name__)


# =====================================================
# ADMIN ROW ACTION MIXIN
# =====================================================

class RowActionMixin:
    """
    Adds Edit / Delete buttons on the right of admin list
    """

    def row_actions(self, obj):
        opts = self.model._meta

        edit_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[obj.pk],
        )
        delete_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_delete",
            args=[obj.pk],
        )

        return format_html(
            """
            <div class="row-actions">
                <a class="button edit" href="{}">Edit</a>
                <a class="button delete" href="{}">Delete</a>
            </div>
            """,
            edit_url,
            delete_url,
        )

    row_actions.short_description = "Actions"


# ======================================================
# BASE RESOURCE (shared safety + normalization)
# ======================================================

class BaseCleanResource(resources.ModelResource):
    """
    Shared import logic:
    - ignores extra columns
    - trims strings
    - safe boolean & integer parsing
    """

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


# ======================================================
# LECTURER MATCHING UTILITY
# ======================================================

class LecturerMatcher:
    """
    Utility class for matching lecturer names with caching for production performance
    """
    
    # Common titles that might appear in names
    TITLES = ['Dr.', 'Dr', 'Prof.', 'Prof', 'Mr.', 'Mr', 'Mrs.', 'Mrs', 'Ms.', 'Ms', 'Miss', 'Eng.', 'Eng']
    
    def __init__(self):
        self._cache = {}
        self._load_cache()
    
    def _load_cache(self):
        """Load all lecturers into cache for faster lookups"""
        lecturers = Lecturer.objects.all().values('id', 'name')
        for lecturer in lecturers:
            # Cache by exact name
            self._cache[lecturer['name']] = lecturer['id']
            # Cache by lowercase name
            self._cache[lecturer['name'].lower()] = lecturer['id']
            
            # Cache without titles
            name_without_title = self._remove_titles(lecturer['name'])
            if name_without_title != lecturer['name']:
                self._cache[name_without_title] = lecturer['id']
                self._cache[name_without_title.lower()] = lecturer['id']
    
    def _remove_titles(self, name):
        """Remove common titles from a name"""
        for title in self.TITLES:
            if name.startswith(title + ' '):
                return name[len(title)+1:].strip()
        return name
    
    def _get_name_parts(self, name):
        """Split name into parts for matching"""
        return [part for part in name.split() if part]
    
    def _matches_initials(self, name1, name2):
        """Check if names match by initials (e.g., 'J. Mumbi' matches 'J. Mumbi')"""
        parts1 = self._get_name_parts(name1)
        parts2 = self._get_name_parts(name2)
        
        if len(parts1) < 2 or len(parts2) < 2:
            return False
        
        # Get first initial and last name
        first_initial1 = parts1[0][0] if parts1[0] else ''
        first_initial2 = parts2[0][0] if parts2[0] else ''
        last_name1 = parts1[-1]
        last_name2 = parts2[-1]
        
        return first_initial1 == first_initial2 and last_name1 == last_name2
    
    def get_lecturer_id(self, value):
        """
        Find lecturer ID by name with multiple matching strategies
        Returns lecturer ID or None
        """
        if not value or (isinstance(value, str) and value.strip() == ''):
            return None
        
        value = value.strip()
        logger.info(f"Looking for lecturer with name: '{value}'")
        
        # Check cache first
        if value in self._cache:
            logger.info(f"Found in cache (exact): {value}")
            return self._cache[value]
        
        if value.lower() in self._cache:
            logger.info(f"Found in cache (lowercase): {value}")
            return self._cache[value.lower()]
        
        # Strategy 1: Exact match on name field
        try:
            lecturer = Lecturer.objects.get(name=value)
            self._cache[value] = lecturer.id
            self._cache[value.lower()] = lecturer.id
            logger.info(f"Found lecturer by exact name: {value}")
            return lecturer.id
        except Lecturer.DoesNotExist:
            pass
        
        # Strategy 2: Case-insensitive exact match
        try:
            lecturer = Lecturer.objects.get(name__iexact=value)
            self._cache[value] = lecturer.id
            self._cache[value.lower()] = lecturer.id
            logger.info(f"Found lecturer by case-insensitive exact match: {value}")
            return lecturer.id
        except Lecturer.DoesNotExist:
            pass
        
        # Strategy 3: Remove titles and match
        value_without_title = self._remove_titles(value)
        
        if value_without_title != value:
            try:
                lecturer = Lecturer.objects.get(name__iexact=value_without_title)
                self._cache[value] = lecturer.id
                self._cache[value.lower()] = lecturer.id
                self._cache[value_without_title] = lecturer.id
                self._cache[value_without_title.lower()] = lecturer.id
                logger.info(f"Found lecturer by name without title: {value_without_title}")
                return lecturer.id
            except Lecturer.DoesNotExist:
                pass
        
        # Strategy 4: Contains match (handles extra titles in DB)
        lecturers = Lecturer.objects.filter(name__icontains=value)
        if lecturers.exists():
            lecturer = lecturers.first()
            self._cache[value] = lecturer.id
            self._cache[value.lower()] = lecturer.id
            logger.info(f"Found lecturer by contains match: {lecturer.name}")
            return lecturer.id
        
        # Strategy 5: Contains match on value without title
        if value_without_title != value:
            lecturers = Lecturer.objects.filter(name__icontains=value_without_title)
            if lecturers.exists():
                lecturer = lecturers.first()
                self._cache[value] = lecturer.id
                self._cache[value.lower()] = lecturer.id
                self._cache[value_without_title] = lecturer.id
                self._cache[value_without_title.lower()] = lecturer.id
                logger.info(f"Found lecturer by contains match (without title): {lecturer.name}")
                return lecturer.id
        
        # Strategy 6: Check all lecturers for fuzzy matches
        for lecturer in Lecturer.objects.all():
            db_name = lecturer.name
            db_name_without_title = self._remove_titles(db_name)
            
            # Check if one contains the other
            if (value in db_name or db_name in value or 
                value_without_title in db_name or db_name in value_without_title or
                value.lower() in db_name.lower() or db_name.lower() in value.lower()):
                self._cache[value] = lecturer.id
                self._cache[value.lower()] = lecturer.id
                logger.info(f"Found lecturer by fuzzy contains: {db_name} matches {value}")
                return lecturer.id
            
            # Check initials match
            if self._matches_initials(db_name_without_title, value_without_title):
                self._cache[value] = lecturer.id
                self._cache[value.lower()] = lecturer.id
                logger.info(f"Found lecturer by initials match: {db_name} matches {value}")
                return lecturer.id
        
        # Strategy 7: Handle "AND" vs "&" variations
        if '&' in value:
            value_with_and = value.replace('&', 'AND')
            lecturers = Lecturer.objects.filter(name__icontains=value_with_and)
            if lecturers.exists():
                lecturer = lecturers.first()
                self._cache[value] = lecturer.id
                self._cache[value.lower()] = lecturer.id
                logger.info(f"Found lecturer by AND/& variation: {lecturer.name}")
                return lecturer.id
        
        if 'AND' in value:
            value_with_amp = value.replace('AND', '&')
            lecturers = Lecturer.objects.filter(name__icontains=value_with_amp)
            if lecturers.exists():
                lecturer = lecturers.first()
                self._cache[value] = lecturer.id
                self._cache[value.lower()] = lecturer.id
                logger.info(f"Found lecturer by AND/& variation: {lecturer.name}")
                return lecturer.id
        
        logger.warning(f"Lecturer with name '{value}' not found in database")
        return None
    
    def get_lecturer_instance(self, value):
        """Get full Lecturer instance by name"""
        lecturer_id = self.get_lecturer_id(value)
        if lecturer_id:
            try:
                return Lecturer.objects.get(id=lecturer_id)
            except Lecturer.DoesNotExist:
                return None
        return None


# ======================================================
# DEPARTMENT MATCHING UTILITY
# ======================================================

class DepartmentMatcher:
    """Utility class for matching department names"""
    
    def __init__(self):
        self._cache = {}
        self._load_cache()
    
    def _load_cache(self):
        departments = Department.objects.all().values('id', 'name')
        for dept in departments:
            self._cache[dept['name']] = dept['id']
            self._cache[dept['name'].lower()] = dept['id']
    
    def get_department_id(self, value):
        """Find department ID by name"""
        if not value or (isinstance(value, str) and value.strip() == ''):
            return None
        
        value = value.strip()
        
        # Check cache
        if value in self._cache:
            return self._cache[value]
        if value.lower() in self._cache:
            return self._cache[value.lower()]
        
        # Try exact match
        try:
            dept = Department.objects.get(name=value)
            self._cache[value] = dept.id
            self._cache[value.lower()] = dept.id
            return dept.id
        except Department.DoesNotExist:
            pass
        
        # Try case-insensitive
        try:
            dept = Department.objects.get(name__iexact=value)
            self._cache[value] = dept.id
            self._cache[value.lower()] = dept.id
            return dept.id
        except Department.DoesNotExist:
            pass
        
        # Try contains
        depts = Department.objects.filter(name__icontains=value)
        if depts.exists():
            dept = depts.first()
            self._cache[value] = dept.id
            self._cache[value.lower()] = dept.id
            return dept.id
        
        return None
    
    def get_department_instance(self, value):
        dept_id = self.get_department_id(value)
        if dept_id:
            try:
                return Department.objects.get(id=dept_id)
            except Department.DoesNotExist:
                return None
        return None


# ======================================================
# PROGRAM MATCHING UTILITY
# ======================================================

class ProgramMatcher:
    """Utility class for matching program names"""
    
    def __init__(self):
        self._cache = {}
        self._load_cache()
    
    def _load_cache(self):
        programs = Program.objects.all().values('id', 'name')
        for prog in programs:
            self._cache[prog['name']] = prog['id']
            self._cache[prog['name'].lower()] = prog['id']
    
    def get_program_id(self, value):
        """Find program ID by name"""
        if not value or (isinstance(value, str) and value.strip() == ''):
            return None
        
        value = value.strip()
        
        # Check cache
        if value in self._cache:
            return self._cache[value]
        if value.lower() in self._cache:
            return self._cache[value.lower()]
        
        # Try exact match
        try:
            prog = Program.objects.get(name=value)
            self._cache[value] = prog.id
            self._cache[value.lower()] = prog.id
            return prog.id
        except Program.DoesNotExist:
            pass
        
        # Try case-insensitive
        try:
            prog = Program.objects.get(name__iexact=value)
            self._cache[value] = prog.id
            self._cache[value.lower()] = prog.id
            return prog.id
        except Program.DoesNotExist:
            pass
        
        # Try contains
        progs = Program.objects.filter(name__icontains=value)
        if progs.exists():
            prog = progs.first()
            self._cache[value] = prog.id
            self._cache[value.lower()] = prog.id
            return prog.id
        
        # Remove group indicators and try again
        without_group = re.sub(r'\s+[Gg]\.?\s*[A-Za-z]|\s+GROUP\s+[A-Za-z]', '', value)
        if without_group != value:
            progs = Program.objects.filter(name__icontains=without_group)
            if progs.exists():
                prog = progs.first()
                self._cache[value] = prog.id
                self._cache[value.lower()] = prog.id
                return prog.id
        
        return None
    
    def get_program_instance(self, value):
        prog_id = self.get_program_id(value)
        if prog_id:
            try:
                return Program.objects.get(id=prog_id)
            except Program.DoesNotExist:
                return None
        return None


# ======================================================
# PROGRAM COURSE MATCHING UTILITY
# ======================================================

class ProgramCourseMatcher:
    """Utility class for matching ProgramCourse by course_code or course_name."""

    def __init__(self):
        self._cache = {}
        # Keyed by normalize_course_code() (whitespace-stripped, upper-cased)
        # -> ProgramCourse.id. Kept separate from self._cache (which holds
        # exact/lower verbatim keys) so a normalized hit can never silently
        # collide with an exact key that happens to look similar.
        self._normalized_cache = {}
        self._load_cache()

    def _load_cache(self):
        courses = ProgramCourse.objects.all().values('id', 'course_code', 'course_name')
        for c in courses:
            self._cache[c['course_code']] = c['id']
            self._cache[c['course_code'].lower()] = c['id']
            self._cache[c['course_name']] = c['id']
            self._cache[c['course_name'].lower()] = c['id']
            norm = normalize_course_code(c['course_code'])
            if norm:
                # First one wins on a collision; genuine duplicates are rare
                # and get_program_course_id() still falls through to DB
                # queries (icontains, etc.) for anything ambiguous.
                self._normalized_cache.setdefault(norm, c['id'])

    def get_program_course_id(self, value):
        if not value or (isinstance(value, str) and not value.strip()):
            return None
        value = value.strip()
        if value in self._cache:
            return self._cache[value]
        if value.lower() in self._cache:
            return self._cache[value.lower()]
        # Whitespace/case-insensitive normalized match. Handles the common
        # case of hand-prepared CSVs dropping the space in codes like
        # "COSC 101" -> "COSC101" -- a plain exact/iexact/icontains check
        # never catches this since "COSC101" is not a substring of
        # "COSC 101". Digits are never altered, so "COSC 0101" and
        # "COSC 00101" still resolve to distinct courses.
        norm = normalize_course_code(value)
        if norm in self._normalized_cache:
            return self._normalized_cache[norm]
        # Exact code match
        try:
            pc = ProgramCourse.objects.get(course_code=value)
            self._cache[value] = pc.id
            return pc.id
        except ProgramCourse.DoesNotExist:
            pass
        # Case-insensitive code
        try:
            pc = ProgramCourse.objects.get(course_code__iexact=value)
            self._cache[value] = pc.id
            return pc.id
        except ProgramCourse.DoesNotExist:
            pass
        # Contains match on code
        pcs = ProgramCourse.objects.filter(course_code__icontains=value)
        if pcs.exists():
            pc = pcs.first()
            self._cache[value] = pc.id
            return pc.id
        # Contains match on name
        pcs = ProgramCourse.objects.filter(course_name__icontains=value)
        if pcs.exists():
            pc = pcs.first()
            self._cache[value] = pc.id
            return pc.id
        return None

    def get_program_course_instance(self, value):
        pc_id = self.get_program_course_id(value)
        if pc_id:
            try:
                return ProgramCourse.objects.get(id=pc_id)
            except ProgramCourse.DoesNotExist:
                return None
        return None


# ======================================================
# IMPORT / EXPORT RESOURCES
# ======================================================

class CourseAllocationResource(resources.ModelResource):
    # Define all fields explicitly with exact model field names as column_name
    course_code = fields.Field(attribute='course_code', column_name='course_code')
    course_name = fields.Field(attribute='course_name', column_name='course_name')
    
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    
    origin_department = fields.Field(
        attribute='origin_department',
        column_name='origin_department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name')
    )
    
    lecturer = fields.Field(
        attribute='lecturer',
        column_name='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'name')
    )
    
    number_of_students = fields.Field(
        attribute='number_of_students',
        column_name='number_of_students'
    )
    
    approved_by_dvc = fields.Field(
        attribute='approved_by_dvc',
        column_name='approved_by_dvc',
        widget=BooleanWidget()
    )
    
    rejected_by_dvc = fields.Field(
        attribute='rejected_by_dvc',
        column_name='rejected_by_dvc',
        widget=BooleanWidget()
    )
    
    reason_for_disapproval = fields.Field(
        attribute='reason_for_disapproval',
        column_name='reason_for_disapproval'
    )
    
    submitted_to_tt = fields.Field(
        attribute='submitted_to_tt',
        column_name='submitted_to_tt',
        widget=BooleanWidget()
    )

    program_course = fields.Field(
        attribute='program_course',
        column_name='program_course',
        widget=ForeignKeyWidget(ProgramCourse, 'course_code')
    )

    class Meta:
        model = CourseAllocation
        import_id_fields = []
        fields = (
            'course_code',
            'course_name',
            'department',
            'origin_department',
            'program',
            'lecturer',
            'number_of_students',
            'approved_by_dvc',
            'rejected_by_dvc',
            'reason_for_disapproval',
            'submitted_to_tt',
            'program_course',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initialize matchers for performance
        self.lecturer_matcher = LecturerMatcher()
        self.department_matcher = DepartmentMatcher()
        self.program_matcher = ProgramMatcher()
        self.program_course_matcher = ProgramCourseMatcher()

    def before_import_row(self, row, **kwargs):
        # Clean string fields
        for key in ['course_code', 'course_name', 'reason_for_disapproval']:
            if key in row and isinstance(row[key], str):
                row[key] = row[key].strip()
        
        # Convert course code to uppercase
        if 'course_code' in row:
            row['course_code'] = str(row['course_code']).upper().strip()
        
        # Handle empty strings for foreign keys - convert to None
        for key in ['lecturer', 'department', 'origin_department', 'program', 'program_course']:
            if key in row and (row[key] is None or (isinstance(row[key], str) and row[key].strip() == '')):
                row[key] = None

    def get_instance(self, instance_loader, row):
        """
        Override get_instance to find the correct instance based on 
        course_code, department, and program combination
        """
        course_code = row.get('course_code', '').strip().upper()
        department_name = row.get('department', '').strip()
        program_name = row.get('program', '').strip()
        
        # Skip if essential fields are missing
        if not course_code or not department_name or not program_name:
            return None
        
        # Get department and program instances using matchers
        department = self.department_matcher.get_department_instance(department_name)
        program = self.program_matcher.get_program_instance(program_name)
        
        if not department or not program:
            return None
        
        try:
            # Try to find by course_code, department, and program
            return CourseAllocation.objects.get(
                course_code=course_code,
                department=department,
                program=program
            )
        except (CourseAllocation.DoesNotExist, CourseAllocation.MultipleObjectsReturned):
            return None

    def dehydrate_lecturer(self, allocation):
        """Custom export to show lecturer name properly"""
        if allocation.lecturer:
            return allocation.lecturer.name
        return ""

    def dehydrate_program_course(self, allocation):
        """Custom export to show program course code"""
        if allocation.program_course:
            return allocation.program_course.course_code
        return ""

    def import_row(self, row, instance_loader, **kwargs):
        """
        Custom import_row to handle duplicate course codes by adding suffixes
        when the same course code is used for different department/program combinations
        """
        # Clean and prepare the row data
        self.before_import_row(row, **kwargs)
        
        course_code = row.get('course_code', '').strip().upper()
        department_name = row.get('department', '').strip()
        program_name = row.get('program', '').strip()
        
        # Log the import attempt
        logger.info(f"Importing row - Course: {course_code}, Department: {department_name}, Program: {program_name}")
        
        # Skip if essential fields are missing
        if not course_code:
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_SKIP
            row_result.diff = ["Skipped - Missing course_code"]
            return row_result
        
        if not department_name:
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_SKIP
            row_result.diff = [f"Skipped - Missing department for course {course_code}"]
            return row_result
        
        if not program_name:
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_SKIP
            row_result.diff = [f"Skipped - Missing program for course {course_code}"]
            return row_result
        
        # Get related instances using matchers
        department = self.department_matcher.get_department_instance(department_name)
        if not department:
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_ERROR
            row_result.errors = [f"Department '{department_name}' not found in database for course {course_code}"]
            return row_result
        
        program = self.program_matcher.get_program_instance(program_name)
        if not program:
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_ERROR
            row_result.errors = [f"Program '{program_name}' not found in database for course {course_code}"]
            return row_result
        
        lecturer = self.lecturer_matcher.get_lecturer_instance(row.get('lecturer', ''))
        origin_department = self.department_matcher.get_department_instance(row.get('origin_department', ''))
        program_course = self.program_course_matcher.get_program_course_instance(row.get('program_course', ''))
        
        # Log lecturer lookup result
        if lecturer:
            logger.info(f"Lecturer found: {lecturer.name} (ID: {lecturer.id}) for value '{row.get('lecturer', '')}'")
        else:
            logger.info(f"No lecturer found for value: '{row.get('lecturer', '')}'")
        
        # Parse numeric and boolean values
        try:
            number_of_students = int(float(row.get('number_of_students', 0)))
        except (ValueError, TypeError):
            number_of_students = 0
            
        approved_by_dvc = self._parse_boolean(row.get('approved_by_dvc', False))
        rejected_by_dvc = self._parse_boolean(row.get('rejected_by_dvc', False))
        submitted_to_tt = self._parse_boolean(row.get('submitted_to_tt', 1))
        reason_for_disapproval = row.get('reason_for_disapproval', 'No reason yet')
        
        # Check if this exact combination already exists
        try:
            existing = CourseAllocation.objects.get(
                course_code=course_code,
                department=department,
                program=program
            )
            
            # Update existing record
            existing.course_name = row.get('course_name', existing.course_name)
            if origin_department:
                existing.origin_department = origin_department
            existing.lecturer = lecturer  # Will be None if not found (allows clearing)
            existing.number_of_students = number_of_students
            existing.approved_by_dvc = approved_by_dvc
            existing.rejected_by_dvc = rejected_by_dvc
            existing.submitted_to_tt = submitted_to_tt
            existing.reason_for_disapproval = reason_for_disapproval
            if program_course is not None:
                existing.program_course = program_course

            existing.save()
            
            logger.info(f"Updated existing allocation: {course_code} - lecturer set to: {lecturer}")
            
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_UPDATE
            row_result.diff = [f"Updated {course_code} for {department.name} - {program.name}"]
            row_result.instance = existing
            return row_result
            
        except CourseAllocation.DoesNotExist:
            # Check if this course code exists for a different department/program
            different_combination = CourseAllocation.objects.filter(
                course_code=course_code
            ).exists()
            
            final_course_code = course_code
            if different_combination:
                # Find available suffix (A-Z)
                suffix = 'A'
                for i in range(26):  # A to Z
                    test_code = f"{course_code}_{chr(65 + i)}"
                    if not CourseAllocation.objects.filter(
                        course_code=test_code,
                        department=department,
                        program=program
                    ).exists():
                        final_course_code = test_code
                        break
            
            # Create a new instance
            allocation = CourseAllocation(
                course_code=final_course_code,
                course_name=row.get('course_name', ''),
                department=department,
                program=program,
                origin_department=origin_department,
                lecturer=lecturer,
                number_of_students=number_of_students,
                approved_by_dvc=approved_by_dvc,
                rejected_by_dvc=rejected_by_dvc,
                submitted_to_tt=submitted_to_tt,
                reason_for_disapproval=reason_for_disapproval,
                program_course=program_course,
            )
            
            allocation.save()
            
            logger.info(f"Created new allocation: {final_course_code}")
            
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_NEW
            row_result.diff = [f"Created {final_course_code} for {department.name} - {program.name}"]
            row_result.instance = allocation
            return row_result
        
        except CourseAllocation.MultipleObjectsReturned:
            row_result = RowResult()
            row_result.import_type = RowResult.IMPORT_TYPE_ERROR
            row_result.errors = [f"Multiple records found for {course_code} with {department.name} - {program.name}"]
            return row_result

    def _parse_boolean(self, value):
        """Parse boolean values from various formats"""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            value_lower = value.lower().strip()
            return value_lower in ['true', '1', 'yes', 'on', 'y', 't']
        return False


class LabAllocationResource(resources.ModelResource):
    """
    Import / export resource for LabAllocation.

    M2M fields (venues, additional_courses) are serialised as
    pipe-separated codes / course-codes on export, and parsed back on import.

    CSV column layout
    -----------------
    program_course | additional_courses | venues | lecturer |
    number_of_students | is_workshop_course | created_at
    """

    program_course = fields.Field(
        attribute='program_course',
        column_name='program_course',
        widget=ForeignKeyWidget(ProgramCourse, 'course_code'),
    )

    # M2M — exported as pipe-separated course_codes, e.g. "CS101|CS102"
    additional_courses = fields.Field(
        column_name='additional_courses',
        attribute=None,         # handled manually
    )

    # M2M — exported as pipe-separated venue codes, e.g. "CS LAB 1|CS LAB 2"
    venues = fields.Field(
        column_name='venues',
        attribute=None,         # handled manually
    )

    lecturer = fields.Field(
        attribute='lecturer',
        column_name='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'name'),
    )

    number_of_students = fields.Field(
        attribute='number_of_students',
        column_name='number_of_students',
    )

    is_workshop_course = fields.Field(
        attribute='is_workshop_course',
        column_name='is_workshop_course',
        widget=BooleanWidget(),
    )

    created_at = fields.Field(
        attribute='created_at',
        column_name='created_at',
        widget=DateWidget(),
    )

    class Meta:
        model = LabAllocation
        import_id_fields = ('program_course',)
        fields = (
            'program_course',
            'additional_courses',
            'venues',
            'lecturer',
            'number_of_students',
            'is_workshop_course',
            'created_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lecturer_matcher = LecturerMatcher()
        self.program_course_matcher = ProgramCourseMatcher()

    # ── export helpers ────────────────────────────────────────────────────────

    def dehydrate_venues(self, allocation):
        """Export venues as pipe-separated codes."""
        return "|".join(allocation.venues.values_list("code", flat=True))

    def dehydrate_additional_courses(self, allocation):
        """Export additional courses as pipe-separated course codes."""
        return "|".join(
            allocation.additional_courses.values_list("course_code", flat=True)
        )

    # ── import helpers ────────────────────────────────────────────────────────

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()
        # Normalise is_workshop_course
        if 'is_workshop_course' in row:
            row['is_workshop_course'] = str(row['is_workshop_course']).strip().lower() in (
                'true', '1', 'yes', 'y',
            )

    def after_save_instance(self, instance, row, **kwargs):
        """Save M2M relations after the instance has been saved (has a PK)."""
        # venues
        venue_raw = row.get('venues', '') or ''
        if venue_raw:
            codes = [c.strip() for c in venue_raw.split('|') if c.strip()]
            venues = LabVenue.objects.filter(code__in=codes)
            instance.venues.set(venues)

        # additional_courses
        courses_raw = row.get('additional_courses', '') or ''
        if courses_raw:
            course_codes = [c.strip() for c in courses_raw.split('|') if c.strip()]
            pcs = ProgramCourse.objects.filter(course_code__in=course_codes)
            instance.additional_courses.set(pcs)

    def get_lecturer_instance(self, value):
        return self.lecturer_matcher.get_lecturer_instance(value)


class SubmissionControlResource(resources.ModelResource):
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    
    allow_submission_to_dvc = fields.Field(
        attribute='allow_submission_to_dvc',
        column_name='allow_submission_to_dvc',
        widget=BooleanWidget()
    )
    
    allow_submission_to_tt = fields.Field(
        attribute='allow_submission_to_tt',
        column_name='allow_submission_to_tt',
        widget=BooleanWidget()
    )
    
    updated_at = fields.Field(
        attribute='updated_at',
        column_name='updated_at',
        widget=DateWidget()
    )

    class Meta:
        model = SubmissionControl
        import_id_fields = ('department',)
        fields = (
            'department',
            'allow_submission_to_dvc',
            'allow_submission_to_tt',
            'updated_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.department_matcher = DepartmentMatcher()

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()

    def get_department_instance(self, value):
        return self.department_matcher.get_department_instance(value)


class SelectionGroupResource(resources.ModelResource):
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name'),
    )
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name'),
    )
    created_by = fields.Field(
        attribute='created_by',
        column_name='created_by',
        widget=ForeignKeyWidget(User, 'username'),
    )

    class Meta:
        model = SelectionGroup
        import_id_fields = ('name', 'department')
        fields = ('id', 'name', 'department', 'program', 'created_by', 'created_at', 'updated_at')
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


class LecturerCourseMappingResource(resources.ModelResource):
    lecturer = fields.Field(
        attribute='lecturer',
        column_name='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'name'),
    )
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name'),
    )
    # M2M — exported as pipe-separated course_codes, e.g. "CS101|CS102|CS201"
    courses = fields.Field(
        column_name='courses',
        attribute=None,  # handled manually
    )

    class Meta:
        model = LecturerCourseMapping
        import_id_fields = ('lecturer', 'department')
        fields = ('id', 'lecturer', 'department', 'courses', 'notes', 'created_at', 'updated_at')
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lecturer_matcher = LecturerMatcher()
        self.department_matcher = DepartmentMatcher()
        self.program_course_matcher = ProgramCourseMatcher()

    # ── export helper ─────────────────────────────────────────────────────────

    def dehydrate_courses(self, mapping):
        """Export mapped courses as pipe-separated course_codes."""
        return "|".join(
            mapping.courses.values_list("course_code", flat=True)
        )

    # ── import helpers ────────────────────────────────────────────────────────

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()

    def after_save_instance(self, instance, row, **kwargs):
        """Save M2M courses after the instance is saved (has a PK).

        Uses after_save_instance instead of the deprecated after_import_instance
        which no longer receives the row in django-import-export >= 3.
        Supports both pipe-separated (our export format) and comma-separated
        (manually prepared CSV) course codes.

        Matching is done in two passes:
          1. Fast exact-code match (covers the common case, including our own
             dehydrate_courses() export round-trip).
          2. Fallback through ProgramCourseMatcher for anything the exact pass
             missed -- this is case-insensitive and also tries the code with
             any shared-course disambiguation tag (e.g. "COSC106(ODEL)")
             stripped off, since hand-prepared CSVs rarely include that tag.

        Previously this only did the exact-match pass, so any CSV whose codes
        didn't byte-for-byte match ProgramCourse.course_code (different case,
        stray whitespace, or a missing "(TAG)" suffix) silently produced an
        empty M2M set -- the lecturer row was created/updated but with 0
        courses, with only a debug-log warning and no visible error in the
        admin import screen.
        """
        courses_raw = row.get('courses', '') or ''
        if not courses_raw:
            instance.courses.set([])
            return

        separator = '|' if '|' in courses_raw else ','
        course_codes = [c.strip() for c in courses_raw.split(separator) if c.strip()]

        pcs = []
        matched_input_codes = set()

        # Pass 1: exact match, case-sensitive (cheap, single query)
        exact_by_code = {
            pc.course_code: pc
            for pc in ProgramCourse.objects.filter(course_code__in=course_codes)
        }
        for code in course_codes:
            if code in exact_by_code:
                pcs.append(exact_by_code[code])
                matched_input_codes.add(code)

        # Pass 2: fuzzy fallback (case-insensitive, tag-stripped, contains)
        # for anything the exact pass missed
        for code in course_codes:
            if code in matched_input_codes:
                continue
            pc = self.program_course_matcher.get_program_course_instance(code)
            if pc is None:
                stripped = strip_course_code_tag(code)
                if stripped != code:
                    pc = self.program_course_matcher.get_program_course_instance(stripped)
            if pc is not None:
                pcs.append(pc)
                matched_input_codes.add(code)

        instance.courses.set(pcs)

        unmatched = [c for c in course_codes if c not in matched_input_codes]
        if unmatched:
            logger.warning(
                f"LecturerCourseMapping import: unrecognised course codes "
                f"for {instance}: {unmatched}"
            )
        # Stash on self (per-import, keyed by row identity) so after_import_row
        # can surface it on the admin's import confirmation screen instead of
        # only in the log file.
        self._unmatched_by_row_id = getattr(self, '_unmatched_by_row_id', {})
        self._unmatched_by_row_id[id(row)] = unmatched

    def after_import_row(self, row, row_result, **kwargs):
        """Append any unmatched-course-code warning to this row's diff so it
        shows up on the admin import preview/result screen, not just the log.
        """
        unmatched = getattr(self, '_unmatched_by_row_id', {}).pop(id(row), None)
        if unmatched:
            note = f"⚠ Unmatched course codes (not linked): {', '.join(unmatched)}"
            if row_result.diff:
                row_result.diff.append(note)
            else:
                row_result.diff = [note]


class ProgramEnrollmentResource(resources.ModelResource):
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name'),
    )

    class Meta:
        model = ProgramEnrollment
        import_id_fields = ('program', 'entry_year')
        fields = ('id', 'program', 'entry_year', 'number_of_students')
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()


class ArchivedCourseAllocationResource(resources.ModelResource):
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    
    semester = fields.Field(
        attribute='semester',
        column_name='semester'
    )
    
    course_code = fields.Field(
        attribute='course_code',
        column_name='course_code'
    )
    
    course_name = fields.Field(
        attribute='course_name',
        column_name='course_name'
    )
    
    origin_department = fields.Field(
        attribute='origin_department',
        column_name='origin_department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name')
    )
    
    lecturer = fields.Field(
        attribute='lecturer',
        column_name='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'name')
    )
    
    number_of_students = fields.Field(
        attribute='number_of_students',
        column_name='number_of_students'
    )
    
    approved_by_dvc = fields.Field(
        attribute='approved_by_dvc',
        column_name='approved_by_dvc',
        widget=BooleanWidget()
    )
    
    rejected_by_dvc = fields.Field(
        attribute='rejected_by_dvc',
        column_name='rejected_by_dvc',
        widget=BooleanWidget()
    )
    
    reason_for_disapproval = fields.Field(
        attribute='reason_for_disapproval',
        column_name='reason_for_disapproval'
    )
    
    submitted_to_tt = fields.Field(
        attribute='submitted_to_tt',
        column_name='submitted_to_tt',
        widget=BooleanWidget()
    )
    
    archived_by = fields.Field(
        attribute='archived_by',
        column_name='archived_by',
        widget=ForeignKeyWidget(User, 'username')
    )
    
    archived_at = fields.Field(
        attribute='archived_at',
        column_name='archived_at',
        widget=DateWidget()
    )

    class Meta:
        model = ArchivedCourseAllocation
        import_id_fields = ('department', 'semester', 'course_code')
        fields = (
            'department',
            'semester',
            'course_code',
            'course_name',
            'origin_department',
            'program',
            'lecturer',
            'number_of_students',
            'approved_by_dvc',
            'rejected_by_dvc',
            'reason_for_disapproval',
            'submitted_to_tt',
            'archived_by',
            'archived_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lecturer_matcher = LecturerMatcher()
        self.department_matcher = DepartmentMatcher()
        self.program_matcher = ProgramMatcher()

    def before_import_row(self, row, **kwargs):
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value.strip()

    def get_lecturer_instance(self, value):
        return self.lecturer_matcher.get_lecturer_instance(value)

    def get_department_instance(self, value):
        return self.department_matcher.get_department_instance(value)

    def get_program_instance(self, value):
        return self.program_matcher.get_program_instance(value)


# ======================================================
# NEW RESOURCES — StudentGroup / GroupingTemplate family /
# SpecialIntakeGroup / BaseSelection / AllocationConfig /
# CombinedCourseGroup / CourseCombinationTemplate family
# ======================================================

class StudentGroupResource(BaseCleanResource):
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name'),
    )
    created_by = fields.Field(
        attribute='created_by',
        column_name='created_by',
        widget=ForeignKeyWidget(User, 'username'),
    )

    class Meta:
        model = StudentGroup
        import_id_fields = ('program', 'year', 'semester', 'intake', 'letter')
        fields = (
            'id', 'program', 'year', 'semester', 'intake',
            'name', 'letter', 'created_by', 'created_at', 'updated_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class GroupingTemplateResource(BaseCleanResource):
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name'),
    )
    created_by = fields.Field(
        attribute='created_by',
        column_name='created_by',
        widget=ForeignKeyWidget(User, 'username'),
    )

    class Meta:
        model = GroupingTemplate
        import_id_fields = ('program', 'year', 'semester', 'intake')
        fields = (
            'id', 'program', 'year', 'semester', 'intake', 'scope',
            'created_by', 'created_at', 'updated_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class GroupingTemplateGroupResource(BaseCleanResource):
    template = fields.Field(
        attribute='template',
        column_name='template_id',
        widget=ForeignKeyWidget(GroupingTemplate),
    )

    class Meta:
        model = GroupingTemplateGroup
        import_id_fields = ('template', 'letter')
        fields = ('id', 'template', 'letter', 'name')
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class GroupingTemplateCourseResource(BaseCleanResource):
    template = fields.Field(
        attribute='template',
        column_name='template_id',
        widget=ForeignKeyWidget(GroupingTemplate),
    )

    class Meta:
        model = GroupingTemplateCourse
        import_id_fields = ('template', 'base_course_code')
        fields = ('id', 'template', 'base_course_code')
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class SpecialIntakeGroupResource(BaseCleanResource):
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name'),
    )

    class Meta:
        model = SpecialIntakeGroup
        import_id_fields = ('program', 'year', 'semester', 'entry_year')
        fields = (
            'id', 'program', 'year', 'semester', 'entry_year',
            'number_of_students', 'academic_year', 'created_at', 'updated_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class BaseSelectionResource(BaseCleanResource):
    program_course = fields.Field(
        attribute='program_course',
        column_name='program_course',
        widget=ForeignKeyWidget(ProgramCourse, 'course_code'),
    )
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name'),
    )
    created_by = fields.Field(
        attribute='created_by',
        column_name='created_by',
        widget=ForeignKeyWidget(User, 'username'),
    )

    class Meta:
        model = BaseSelection
        import_id_fields = ('program_course', 'department')
        fields = ('id', 'program_course', 'department', 'created_by', 'created_at')
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class AllocationConfigResource(BaseCleanResource):
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name'),
    )

    class Meta:
        model = AllocationConfig
        import_id_fields = ('department',)
        fields = (
            'id', 'department',
            'max_load_per_semester', 'max_load_enabled',
            'split_threshold', 'split_extend_by', 'splitting_enabled',
            'pg_designations', 'allowed_semesters', 'updated_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class CombinedCourseGroupResource(BaseCleanResource):
    lecturer = fields.Field(
        attribute='lecturer',
        column_name='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'name'),
    )
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name'),
    )
    origin_department = fields.Field(
        attribute='origin_department',
        column_name='origin_department',
        widget=ForeignKeyWidget(Department, 'name'),
    )
    primary_allocation = fields.Field(
        attribute='primary_allocation',
        column_name='primary_allocation_id',
        widget=ForeignKeyWidget(CourseAllocation),
    )
    created_by = fields.Field(
        attribute='created_by',
        column_name='created_by',
        widget=ForeignKeyWidget(User, 'username'),
    )

    class Meta:
        model = CombinedCourseGroup
        import_id_fields = ('group_code',)
        # NOTE: 'allocations' (M2M) is intentionally left out of import/export
        # here, same as SelectionGroup.courses — manage the actual member
        # allocations via the filter_horizontal widget in the admin form.
        fields = (
            'id', 'group_code', 'base_course_code', 'lecturer',
            'primary_allocation', 'department', 'origin_department',
            'split_from_group_code', 'split_from_group_id',
            'created_by', 'created_at', 'updated_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class CourseCombinationTemplateResource(BaseCleanResource):
    department = fields.Field(
        attribute='department',
        column_name='department',
        widget=ForeignKeyWidget(Department, 'name'),
    )
    lecturer = fields.Field(
        attribute='lecturer',
        column_name='lecturer',
        widget=ForeignKeyWidget(Lecturer, 'name'),
    )
    created_by = fields.Field(
        attribute='created_by',
        column_name='created_by',
        widget=ForeignKeyWidget(User, 'username'),
    )

    class Meta:
        model = CourseCombinationTemplate
        import_id_fields = ('department', 'base_course_code')
        fields = (
            'id', 'base_course_code', 'department', 'lecturer',
            'created_by', 'created_at', 'updated_at',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True


class CourseCombinationTemplateProgramResource(BaseCleanResource):
    template = fields.Field(
        attribute='template',
        column_name='template_id',
        widget=ForeignKeyWidget(CourseCombinationTemplate),
    )
    program = fields.Field(
        attribute='program',
        column_name='program',
        widget=ForeignKeyWidget(Program, 'name'),
    )

    class Meta:
        model = CourseCombinationTemplateProgram
        import_id_fields = ('template', 'program')
        fields = ('id', 'template', 'program')
        export_order = fields
        skip_unchanged = True
        report_skipped = True


# ======================================================
# ADMIN CONFIGURATIONS
# ======================================================

@admin.register(CourseAllocation)
class CourseAllocationAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = CourseAllocationResource

    list_display = (
        "course_code",
        "course_name",
        "department",
        "program",
        "program_course_display",
        "lecturer_display",
        "status_badge",
        "number_of_students",
        "submitted_to_tt",
        "row_actions",
    )
    list_display_links = ("course_code",)

    list_filter = (
        "department",
        "program",
        "program_course",
        "approved_by_dvc",
        "rejected_by_dvc",
        "submitted_to_tt",
    )
    search_fields = (
        "course_code",
        "course_name",
        "lecturer__payroll_number",
        "lecturer__name",
        "lecturer__user__first_name",
        "lecturer__user__last_name",
        "department__name",
        "program__name",
        "program_course__course_code",
        "program_course__course_name",
    )
    ordering = ("course_code",)

    def program_course_display(self, obj):
        """Display linked ProgramCourse code and name"""
        if obj.program_course:
            return f"{obj.program_course.course_code} — {obj.program_course.course_name}"
        return "—"

    program_course_display.short_description = "Program Course"
    program_course_display.admin_order_field = "program_course__course_code"

    def lecturer_display(self, obj):
        """Display lecturer name using the display_name property"""
        if obj.lecturer:
            return obj.lecturer.display_name
        return "Not Assigned"

    lecturer_display.short_description = "Lecturer"
    lecturer_display.admin_order_field = "lecturer__name"

    def status_badge(self, obj):
        label = obj.status_label()
        color = (
            "#2196F3" if obj.submitted_to_tt else
            "#4CAF50" if obj.approved_by_dvc else
            "#F44336" if obj.rejected_by_dvc else
            "#FF9800"
        )
        return format_html(
            '<span style="background:{};color:white;padding:4px 10px;border-radius:12px;">{}</span>',
            color,
            label,
        )

    status_badge.short_description = "Status"

    def get_export_filename(self, request, queryset, file_format):
        return f"course_allocations.{file_format.get_extension()}"

    def get_import_resource_kwargs(self, request, *args, **kwargs):
        return {}

    def get_export_resource_kwargs(self, request, *args, **kwargs):
        return {}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "department", "origin_department", "program",
            "program_course", "lecturer", "lecturer__user"
        )

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(LabAllocation)
class LabAllocationAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = LabAllocationResource

    list_display = (
        "primary_course_display",
        "additional_courses_display",
        "venues_display",
        "lecturer",
        "number_of_students",
        "is_workshop_badge",
        "created_at",
        "row_actions",
    )
    list_display_links = ("primary_course_display",)

    list_filter = ("is_workshop_course", "lecturer")
    search_fields = (
        "program_course__course_code",
        "program_course__course_name",
        "additional_courses__course_code",
        "venues__code",
        "lecturer__payroll_number",
        "lecturer__name",
    )
    filter_horizontal = ("venues", "additional_courses")
    readonly_fields   = ("created_at",)

    fieldsets = (
        ("Primary Course", {
            "fields": ("program_course",),
        }),
        ("Additional Courses", {
            "fields": ("additional_courses",),
            "description": "Extra course codes that share this lab / workshop allocation.",
        }),
        ("Venues", {
            "fields": ("venues",),
            "description": (
                "Select one or more candidate venues. "
                "The autoscheduler picks the smallest available one per session."
            ),
        }),
        ("Details", {
            "fields": ("lecturer", "number_of_students", "is_workshop_course", "created_at"),
        }),
    )

    # ── custom display columns ────────────────────────────────────────────────

    def primary_course_display(self, obj):
        return obj.program_course.course_code if obj.program_course else "—"
    primary_course_display.short_description = "Primary Course"
    primary_course_display.admin_order_field = "program_course__course_code"

    def additional_courses_display(self, obj):
        codes = list(obj.additional_courses.values_list("course_code", flat=True))
        if not codes:
            return format_html('<span style="color:#999;">—</span>')
        return ", ".join(codes)
    additional_courses_display.short_description = "Additional Courses"

    def venues_display(self, obj):
        codes = list(obj.venues.values_list("code", flat=True))
        if not codes:
            return format_html('<span style="color:#999;">—</span>')
        return ", ".join(codes)
    venues_display.short_description = "Venues"

    def is_workshop_badge(self, obj):
        if obj.is_workshop_course:
            return format_html(
                '<span style="background:#7b2d8b;color:white;padding:3px 10px;'
                'border-radius:12px;font-weight:bold;">Workshop</span>'
            )
        return format_html(
            '<span style="background:#1e4275;color:white;padding:3px 10px;'
            'border-radius:12px;font-weight:bold;">Lab</span>'
        )
    is_workshop_badge.short_description = "Type"

    # ── queryset ──────────────────────────────────────────────────────────────

    def get_export_filename(self, request, queryset, file_format):
        return f"lab_allocations.{file_format.get_extension()}"

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related("program_course", "lecturer")
            .prefetch_related("venues", "additional_courses")
        )

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js  = ("admin/row_click.js",)


@admin.register(SubmissionControl)
class SubmissionControlAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = SubmissionControlResource

    list_display = (
        "department",
        "allow_submission_to_dvc",
        "allow_submission_to_tt",
        "updated_at",
        "row_actions",
    )
    list_display_links = ("department",)

    ordering = ("department__name",)
    
    def get_export_filename(self, request, queryset, file_format):
        return f"submission_controls.{file_format.get_extension()}"
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("department")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(ArchivedCourseAllocation)
class ArchivedCourseAllocationAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = ArchivedCourseAllocationResource

    list_display = (
        "semester",
        "course_code",
        "course_name",
        "department",
        "program",
        "lecturer",
        "archived_at",
        "archived_by",
        "row_actions",
    )
    list_display_links = ("course_code", "semester")

    list_filter = ("semester", "department")
    search_fields = ("course_code", "course_name", "semester")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_export_filename(self, request, queryset, file_format):
        return f"archived_course_allocations.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "department",
            "origin_department",
            "program",
            "lecturer",
            "archived_by",
        )

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)

# ── SelectionGroup ──────────────────────────────────────────────────────────

@admin.register(SelectionGroup)
class SelectionGroupAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = SelectionGroupResource

    list_display = (
        "name",
        "department",
        "program",
        "course_count",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("name",)

    list_filter = ("department", "program")
    search_fields = ("name", "department__name", "program__name")
    filter_horizontal = ("courses",)
    ordering = ("department", "name")
    readonly_fields = ("created_at", "updated_at")

    def course_count(self, obj):
        return obj.courses.count()

    course_count.short_description = "# Courses"

    def get_export_filename(self, request, queryset, file_format):
        return f"selection_groups.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "department", "program", "created_by"
        ).prefetch_related("courses")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── SpecializationCategory / SpecializationStem ─────────────────────────────

class SpecializationStemInline(admin.TabularInline):
    model = SpecializationStem
    extra = 0
    fields = ("name", "course_count_display", "created_by")
    readonly_fields = ("course_count_display",)
    filter_horizontal = ()
    show_change_link = True

    def course_count_display(self, obj):
        return obj.courses.count() if obj.pk else 0

    course_count_display.short_description = "# Courses"


@admin.register(SpecializationCategory)
class SpecializationCategoryAdmin(RowActionMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "department",
        "program",
        "year",
        "semester",
        "stem_count",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("name",)
    list_filter = ("department", "program", "year", "semester")
    search_fields = ("name", "department__name", "program__name")
    ordering = ("department", "program", "year", "semester", "name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [SpecializationStemInline]

    def stem_count(self, obj):
        return obj.stems.count()

    stem_count.short_description = "# Stems"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "department", "program", "created_by"
        ).prefetch_related("stems")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(SpecializationStem)
class SpecializationStemAdmin(RowActionMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "get_department",
        "get_program",
        "course_count",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("name",)
    list_filter = ("category__department", "category__program", "category")
    search_fields = ("name", "category__name", "category__program__name")
    filter_horizontal = ("courses",)
    ordering = ("category", "name")
    readonly_fields = ("created_at", "updated_at")

    def get_department(self, obj):
        return obj.category.department

    get_department.short_description = "Department"

    def get_program(self, obj):
        return obj.category.program

    get_program.short_description = "Program"

    def course_count(self, obj):
        return obj.courses.count()

    course_count.short_description = "# Courses"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "category", "category__department", "category__program", "created_by"
        ).prefetch_related("courses")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── LecturerCourseMapping ───────────────────────────────────────────────────

@admin.register(LecturerCourseMapping)
class LecturerCourseMappingAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = LecturerCourseMappingResource

    list_display = (
        "lecturer",
        "department",
        "course_count",
        "notes",
        "created_at",
        "row_actions",
    )
    list_display_links = ("lecturer",)

    list_filter = ("department",)
    search_fields = (
        "lecturer__name",
        "lecturer__payroll_number",
        "department__name",
    )
    filter_horizontal = ("courses",)
    ordering = ("department", "lecturer__name")
    readonly_fields = ("created_at", "updated_at")

    def course_count(self, obj):
        return obj.courses.count()

    course_count.short_description = "# Courses"

    def get_export_filename(self, request, queryset, file_format):
        return f"lecturer_course_mappings.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "lecturer", "department"
        ).prefetch_related("courses")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── ProgramEnrollment ───────────────────────────────────────────────────────

@admin.register(ProgramEnrollment)
class ProgramEnrollmentAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = ProgramEnrollmentResource

    list_display = (
        "program",
        "entry_year",
        "number_of_students",
        "row_actions",
    )
    list_display_links = ("program",)

    list_filter = ("program", "entry_year")
    search_fields = ("program__name",)
    ordering = ("program", "-entry_year")

    def get_export_filename(self, request, queryset, file_format):
        return f"program_enrollments.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("program")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── AcademicYearTracker (singleton "clock") ─────────────────────────────────

@admin.register(AcademicYearTracker)
class AcademicYearTrackerAdmin(admin.ModelAdmin):
    list_display = ("current_year", "updated_at")

    def has_add_permission(self, request):
        # Only one row should ever exist.
        return not AcademicYearTracker.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ── DVCActionLog (event audit trail for submit/approve/reject) ──────────────

@admin.register(DVCActionLog)
class DVCActionLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "department", "action", "alloc_type",
        "course_code", "lecturer", "actor", "reported",
    )
    list_filter = ("action", "alloc_type", "reported", "department")
    search_fields = ("course_code", "course_name", "department__name", "actor__username")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ── StudentGroup ─────────────────────────────────────────────────────────────

@admin.register(StudentGroup)
class StudentGroupAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = StudentGroupResource

    list_display = (
        "display_name",
        "program",
        "year",
        "semester",
        "intake",
        "letter",
        "course_count",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("display_name",)
    list_filter = ("program", "year", "semester", "intake")
    search_fields = ("name", "letter", "program__name")
    ordering = ("program", "year", "semester", "intake", "letter")
    readonly_fields = ("created_at", "updated_at")

    def get_export_filename(self, request, queryset, file_format):
        return f"student_groups.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("program", "created_by")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── GroupingTemplate / Group / Course ────────────────────────────────────────

class GroupingTemplateGroupInline(admin.TabularInline):
    model = GroupingTemplateGroup
    extra = 0
    fields = ("letter", "name")
    ordering = ("letter",)


class GroupingTemplateCourseInline(admin.TabularInline):
    model = GroupingTemplateCourse
    extra = 0
    fields = ("base_course_code",)


@admin.register(GroupingTemplate)
class GroupingTemplateAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = GroupingTemplateResource

    list_display = (
        "program",
        "year",
        "semester",
        "intake",
        "scope",
        "group_count",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("program",)
    list_filter = ("program", "year", "semester", "intake", "scope")
    search_fields = ("program__name",)
    ordering = ("program", "year", "semester", "intake")
    readonly_fields = ("created_at", "updated_at")
    inlines = [GroupingTemplateGroupInline, GroupingTemplateCourseInline]

    def group_count(self, obj):
        return obj.groups.count()

    group_count.short_description = "# Groups"

    def get_export_filename(self, request, queryset, file_format):
        return f"grouping_templates.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "program", "created_by"
        ).prefetch_related("groups", "course_codes")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(GroupingTemplateGroup)
class GroupingTemplateGroupAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = GroupingTemplateGroupResource

    list_display = ("template", "letter", "name", "row_actions")
    list_display_links = ("template",)
    list_filter = ("template__program",)
    search_fields = ("letter", "name", "template__program__name")
    ordering = ("template", "letter")

    def get_export_filename(self, request, queryset, file_format):
        return f"grouping_template_groups.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("template", "template__program")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(GroupingTemplateCourse)
class GroupingTemplateCourseAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = GroupingTemplateCourseResource

    list_display = ("template", "base_course_code", "row_actions")
    list_display_links = ("template",)
    list_filter = ("template__program",)
    search_fields = ("base_course_code", "template__program__name")
    ordering = ("template", "base_course_code")

    def get_export_filename(self, request, queryset, file_format):
        return f"grouping_template_courses.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("template", "template__program")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── SpecialIntakeGroup ───────────────────────────────────────────────────────

@admin.register(SpecialIntakeGroup)
class SpecialIntakeGroupAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = SpecialIntakeGroupResource

    list_display = (
        "program",
        "year",
        "semester",
        "entry_year",
        "academic_year",
        "number_of_students",
        "total_courses",
        "total_electives",
        "row_actions",
    )
    list_display_links = ("program",)
    list_filter = ("program", "year", "semester")
    search_fields = ("program__name", "academic_year")
    ordering = ("program", "-entry_year", "year", "semester")
    readonly_fields = ("academic_year", "created_at", "updated_at")

    def get_export_filename(self, request, queryset, file_format):
        return f"special_intake_groups.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("program")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── BaseSelection ─────────────────────────────────────────────────────────────

@admin.register(BaseSelection)
class BaseSelectionAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = BaseSelectionResource

    list_display = (
        "program_course",
        "department",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("program_course",)
    list_filter = ("department",)
    search_fields = ("program_course__course_code", "program_course__course_name", "department__name")
    ordering = ("department", "program_course__course_code")
    readonly_fields = ("created_at",)

    def get_export_filename(self, request, queryset, file_format):
        return f"base_selections.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "program_course", "department", "created_by"
        )

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── AllocationConfig ──────────────────────────────────────────────────────────

@admin.register(AllocationConfig)
class AllocationConfigAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = AllocationConfigResource

    list_display = (
        "department_display",
        "max_load_per_semester",
        "max_load_enabled",
        "split_threshold",
        "split_extend_by",
        "splitting_enabled",
        "updated_at",
        "row_actions",
    )
    list_display_links = ("department_display",)
    list_filter = ("max_load_enabled", "splitting_enabled")
    search_fields = ("department__name",)
    ordering = ("department",)
    readonly_fields = ("updated_at",)

    def department_display(self, obj):
        return obj.department.name if obj.department else "GLOBAL"

    department_display.short_description = "Department"

    def get_export_filename(self, request, queryset, file_format):
        return f"allocation_configs.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("department")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── CombinedCourseGroup ───────────────────────────────────────────────────────

@admin.register(CombinedCourseGroup)
class CombinedCourseGroupAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = CombinedCourseGroupResource

    list_display = (
        "group_code",
        "base_course_code",
        "lecturer",
        "department",
        "origin_department",
        "primary_allocation",
        "allocation_count",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("group_code",)
    list_filter = ("department", "origin_department")
    search_fields = ("group_code", "base_course_code", "lecturer__name")
    filter_horizontal = ("allocations",)
    ordering = ("base_course_code", "-created_at")
    readonly_fields = ("created_at", "updated_at")

    def allocation_count(self, obj):
        return obj.allocations.count()

    allocation_count.short_description = "# Allocations"

    def get_export_filename(self, request, queryset, file_format):
        return f"combined_course_groups.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "lecturer", "department", "origin_department",
            "primary_allocation", "created_by",
        ).prefetch_related("allocations")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


# ── CourseCombinationTemplate / Program ──────────────────────────────────────

class CourseCombinationTemplateProgramInline(admin.TabularInline):
    model = CourseCombinationTemplateProgram
    extra = 0
    fields = ("program",)


@admin.register(CourseCombinationTemplate)
class CourseCombinationTemplateAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = CourseCombinationTemplateResource

    list_display = (
        "base_course_code",
        "department",
        "lecturer",
        "program_count",
        "created_by",
        "created_at",
        "row_actions",
    )
    list_display_links = ("base_course_code",)
    list_filter = ("department",)
    search_fields = ("base_course_code", "department__name", "lecturer__name")
    ordering = ("department", "base_course_code")
    readonly_fields = ("created_at", "updated_at")
    inlines = [CourseCombinationTemplateProgramInline]

    def program_count(self, obj):
        return obj.programs.count()

    program_count.short_description = "# Programs"

    def get_export_filename(self, request, queryset, file_format):
        return f"course_combination_templates.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "department", "lecturer", "created_by"
        ).prefetch_related("programs")

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)


@admin.register(CourseCombinationTemplateProgram)
class CourseCombinationTemplateProgramAdmin(RowActionMixin, ImportExportModelAdmin):
    resource_class = CourseCombinationTemplateProgramResource

    list_display = ("template", "program", "row_actions")
    list_display_links = ("template",)
    list_filter = ("template__department", "program")
    search_fields = ("template__base_course_code", "program__name")
    ordering = ("template", "program")

    def get_export_filename(self, request, queryset, file_format):
        return f"course_combination_template_programs.{file_format.get_extension()}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "template", "template__department", "program"
        )

    class Media:
        css = {"all": ("admin/row_click.css",)}
        js = ("admin/row_click.js",)