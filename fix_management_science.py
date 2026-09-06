"""
IDEMPOTENT SCRIPT: Fix Management Science course allocations.
Uses the ACTUAL data from the system (CSV) and the Word document as source of truth.
Safe to run multiple times - will not create duplicates or harm data.
Run with: python manage.py shell < fix_management_science_safe_v2.py
"""

import re
import sys
print(">>> fix_management_science.py loaded, starting execution...")
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.core.exceptions import ValidationError

# Try to import from the correct location
try:
    from program_management.models import Program, ProgramCourse
except ImportError:
    try:
        from programs.models import Program, ProgramCourse
    except ImportError:
        try:
            from curriculum.models import Program, ProgramCourse
        except ImportError:
            print("ERROR: Could not find Program or ProgramCourse models.")
            print("Please check your app structure.")
            sys.exit(1)

try:
    from course_allocation.models import (
        CourseAllocation, StudentGroup, SpecializationCategory, 
        SpecializationStem, SelectionGroup
    )
except ImportError:
    print("ERROR: Could not import from course_allocation.models")
    sys.exit(1)

try:
    from department_management.models import Department
except ImportError:
    try:
        from departments.models import Department
    except ImportError:
        print("ERROR: Could not import Department model.")
        sys.exit(1)


def normalize_code(code):
    """Normalize course code - extract the base code without suffixes."""
    if not code:
        return ""
    code = code.strip().upper()
    # Remove suffixes like -Y4S1-B-A, (COM)-A, etc.
    # First remove parenthetical suffixes
    code = re.sub(r'\([^)]*\)', '', code)
    # Remove hyphen suffixes like -Y4S1-B-A, -Y4S1-C-BAF, etc.
    code = re.sub(r'-Y\d+S\d+[-\w]*', '', code)
    code = re.sub(r'-[A-Z]+[-\w]*', '', code)
    # Remove trailing suffixes like -A, -B, -C
    code = re.sub(r'-[A-Z]$', '', code)
    return code.strip()


def get_base_course_code(code):
    """Extract the base course code (e.g., BCOM 411 from BCOM 411-Y4S1-B-A)."""
    if not code:
        return ""
    # First clean any parentheses
    cleaned = re.sub(r'\([^)]*\)', '', code)
    # Then remove any dash suffixes
    base = re.sub(r'-[A-Z0-9-]+$', '', cleaned)
    # Also handle "GROUP A" etc.
    base = re.sub(r'\s+GROUP\s+[A-Z]', '', base)
    # Handle "BCOM 431-Y4S1-B-BAF" -> BCOM 431
    base = re.sub(r'-Y\d+S\d+[-\w]*', '', base)
    return base.strip().upper()


def normalize_lecturer_name(name):
    """Normalize lecturer name for matching."""
    if not name:
        return ""
    # Remove titles like Dr., Prof., Mr., Ms., etc.
    name = re.sub(r'^(Dr\.?|Prof\.?|Mr\.?|Ms\.?|Mrs\.?)\s*', '', name, flags=re.IGNORECASE)
    name = name.strip().upper()
    # Remove extra spaces
    name = re.sub(r'\s+', ' ', name)
    return name


# Cache lookups so we don't hit the DB (or print the same warning) more than
# once per distinct lecturer string across the whole run.
_lecturer_cache = {}
_all_lecturers_cache = None


def _clean_for_match(s):
    """Strip everything except letters/digits and uppercase, so 'Nyang'Ara',
    'NYANGARA', and 'Nyang-Ara' all normalize to the same comparable string."""
    if not s:
        return ""
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def _dept_name(obj):
    """Get a plain string department name whether `obj` is a Department FK
    instance or already a plain string (or None)."""
    if obj is None:
        return ''
    name = getattr(obj, 'name', None)
    return name if name is not None else str(obj)


def _get_all_lecturers():
    """Fetch and cache every Lecturer once - cheap (a few hundred rows) and
    lets us do robust Python-side fuzzy matching instead of DB icontains,
    which chokes on punctuation differences like the apostrophe in Nyang'Ara."""
    global _all_lecturers_cache
    if _all_lecturers_cache is None:
        try:
            from lecturer_portal.models import Lecturer
        except ImportError:
            _all_lecturers_cache = []
        else:
            _all_lecturers_cache = list(Lecturer.objects.all())
    return _all_lecturers_cache


def resolve_lecturer(lecturer_name, department=None):
    """
    Resolve a lecturer string from the Word doc (e.g. 'DR. G. AKENGA', 'L WAWERU',
    'D.NYANGARA') to an actual Lecturer model instance, matching on surname.

    Returns None if the string is blank/placeholder (e.g. 'COMP', 'DBAD') or if
    no confident match is found - callers should treat None as "leave whatever
    lecturer is already set alone" rather than wiping out existing data.
    """
    if not lecturer_name or not lecturer_name.strip():
        return None

    placeholder_values = {'NONE', 'COMP', 'DBAD', 'TBA', 'N/A'}
    if lecturer_name.strip().upper() in placeholder_values:
        return None

    dept_name = _dept_name(department)
    cache_key = (lecturer_name.strip().upper(), dept_name)
    if cache_key in _lecturer_cache:
        return _lecturer_cache[cache_key]

    all_lecturers = _get_all_lecturers()
    if not all_lecturers:
        _lecturer_cache[cache_key] = None
        return None

    # Strip titles, replace dots with spaces so "G.AKENGA" -> "G AKENGA",
    # then take the last token as the surname - the most reliable part of
    # these abbreviated names to match against full names in the system.
    normalized = normalize_lecturer_name(lecturer_name).replace('.', ' ')
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    if not normalized:
        _lecturer_cache[cache_key] = None
        return None

    tokens = normalized.split()
    surname = tokens[-1]
    surname_clean = _clean_for_match(surname)

    candidates = [
        l for l in all_lecturers
        if surname_clean and surname_clean in _clean_for_match(l.name)
    ]

    lecturer_obj = None
    if len(candidates) == 1:
        lecturer_obj = candidates[0]
    elif len(candidates) > 1:
        # Prefer a candidate in the same department when there's ambiguity
        # (e.g. multiple "Kimathi" / "Mwangi" records across departments).
        if department:
            short_dept = dept_name.split('(')[0].strip()
            dept_matches = [c for c in candidates if short_dept.lower() in _dept_name(c.department).lower()]
            if len(dept_matches) == 1:
                lecturer_obj = dept_matches[0]
            elif len(dept_matches) > 1:
                lecturer_obj = dept_matches[0]
                print(f"    ⚠ Multiple '{surname}' matches in {short_dept} for '{lecturer_name}'; using {lecturer_obj.name}")
        if not lecturer_obj:
            lecturer_obj = candidates[0]
            print(f"    ⚠ Multiple lecturers match '{lecturer_name}' ({len(candidates)} found); using {lecturer_obj.name}")

    if not lecturer_obj:
        print(f"    ⚠ Could not match lecturer '{lecturer_name}' to any Lecturer record - leaving unset")

    _lecturer_cache[cache_key] = lecturer_obj
    return lecturer_obj


def get_course_allocations_by_base(program, base_code, intake="normal"):
    """Get all CourseAllocation rows for a base course code."""
    
    # Find all allocations where the normalized code matches
    all_allocations = CourseAllocation.objects.filter(
        program=program,
        intake=intake
    )
    
    matching = []
    for alloc in all_allocations:
        if get_base_course_code(alloc.course_code) == base_code:
            matching.append(alloc)
    
    return matching


def consolidate_allocations(allocations):
    """
    Consolidate multiple CourseAllocation rows for the same course.
    Returns the primary allocation to keep and a list of duplicates to delete.
    """
    if not allocations:
        return None, []
    
    if len(allocations) == 1:
        return allocations[0], []
    
    # Find the "best" allocation to keep - prefer one with the most students,
    # or the one that's already approved, or the most recent
    sorted_allocations = sorted(
        allocations,
        key=lambda a: (
            a.approved_by_dvc,  # Approved is better
            a.number_of_students,  # More students is better
            a.id  # Most recent (assuming higher ID = newer)
        ),
        reverse=True
    )
    
    primary = sorted_allocations[0]
    duplicates = sorted_allocations[1:]
    
    return primary, duplicates


def safe_get_or_create_student_group(program, year, semester, intake, letter, name_override=None):
    """Safely get or create a student group."""
    
    name = name_override or f"Group {letter}"
    
    try:
        group = StudentGroup.objects.get(
            program=program,
            year=year,
            semester=semester,
            intake=intake,
            letter=letter
        )
        return group, False
    except StudentGroup.DoesNotExist:
        group = StudentGroup.objects.create(
            program=program,
            year=year,
            semester=semester,
            intake=intake,
            letter=letter,
            name=name
        )
        return group, True
    except StudentGroup.MultipleObjectsReturned:
        group = StudentGroup.objects.filter(
            program=program,
            year=year,
            semester=semester,
            intake=intake,
            letter=letter
        ).first()
        return group, False


def safe_get_or_create_category(program, name, department, year=None, semester=None):
    """Safely get or create a specialization category."""
    
    try:
        category = SpecializationCategory.objects.get(
            program=program,
            name=name
        )
        return category, False
    except SpecializationCategory.DoesNotExist:
        category = SpecializationCategory.objects.create(
            name=name,
            department=department,
            program=program,
            year=year,
            semester=semester
        )
        return category, True
    except SpecializationCategory.MultipleObjectsReturned:
        category = SpecializationCategory.objects.filter(
            program=program,
            name=name
        ).first()
        return category, False


def safe_get_or_create_stem(category, stem_name):
    """Safely get or create a specialization stem."""
    
    try:
        stem = SpecializationStem.objects.get(
            category=category,
            name=stem_name
        )
        return stem, False
    except SpecializationStem.DoesNotExist:
        stem = SpecializationStem.objects.create(
            category=category,
            name=stem_name
        )
        return stem, True
    except SpecializationStem.MultipleObjectsReturned:
        stem = SpecializationStem.objects.filter(
            category=category,
            name=stem_name
        ).first()
        return stem, False


def safe_get_or_create_selection_group(department, program, name):
    """Safely get or create a selection group."""
    
    try:
        group = SelectionGroup.objects.get(
            department=department,
            name=name
        )
        return group, False
    except SelectionGroup.DoesNotExist:
        group = SelectionGroup.objects.create(
            name=name,
            department=department,
            program=program
        )
        return group, True
    except SelectionGroup.MultipleObjectsReturned:
        group = SelectionGroup.objects.filter(
            department=department,
            name=name
        ).first()
        return group, False


def find_or_create_program_course(program, course_code, course_name, year, semester, unit_type='CORE'):
    """Find or create a ProgramCourse."""
    
    base_code = get_base_course_code(course_code)
    
    try:
        pc = ProgramCourse.objects.get(
            program=program,
            course_code=base_code
        )
        return pc, False
    except ProgramCourse.DoesNotExist:
        pc = ProgramCourse.objects.create(
            program=program,
            course_code=base_code,
            course_name=course_name,
            year=year,
            semester=semester,
            unit_type=unit_type,
            student_cohort='0'  # Legacy
        )
        return pc, True
    except ProgramCourse.MultipleObjectsReturned:
        pc = ProgramCourse.objects.filter(
            program=program,
            course_code=base_code
        ).order_by('-student_cohort').first()
        return pc, False


def update_course_allocation(allocation, updates):
    """Safely update a CourseAllocation."""
    changed = False
    for key, value in updates.items():
        if hasattr(allocation, key):
            current = getattr(allocation, key)
            if current != value:
                setattr(allocation, key, value)
                changed = True
    if changed:
        allocation.save()
    return changed


def find_or_create_allocation(program, course_code, course_name, lecturer, student_count, 
                              student_group=None, stem=None, is_elective=False, is_evening=False):
    """
    Find or create a CourseAllocation for a specific course.
    Consolidates duplicates if they exist.
    """
    
    # Get base code for lookup
    base_code = get_base_course_code(course_code)
    
    # Resolve the lecturer string (e.g. "D.NYANGARA") to a Lecturer instance
    # once, shared by both the update and create paths below.
    lecturer_obj = resolve_lecturer(lecturer, department=program.department)
    
    # Find all existing allocations for this course
    existing = get_course_allocations_by_base(program, base_code)
    
    if existing:
        # Consolidate existing allocations
        primary, duplicates = consolidate_allocations(existing)
        
        # Update the primary with the new values
        updates = {
            'course_name': course_name,
            'number_of_students': student_count,
            'student_group': student_group,
            'specialization_stem': stem,
            'is_elective': is_elective,
            'is_evening_weekend': is_evening
        }
        
        # Only update lecturer if we resolved it to an actual Lecturer instance
        if lecturer_obj is not None:
            updates['lecturer'] = lecturer_obj
        
        if update_course_allocation(primary, updates):
            print(f"    Updated allocation: {primary.course_code} -> {student_count} students")
        
        # Delete duplicates
        for dup in duplicates:
            print(f"    Deleting duplicate: {dup.course_code}")
            dup.delete()
            # Note: We can't track this easily without a global counter
        
        return primary, False
    else:
        # Determine year from course code
        year = 4
        if base_code.startswith('BCOM 3') or base_code.startswith('BPLM 3') or base_code == 'COSC 260':
            year = 3
        
        # Create new allocation
        # First ensure ProgramCourse exists
        pc, _ = find_or_create_program_course(
            program, 
            base_code, 
            course_name,
            year=year,
            semester=1
        )
        
        # lecturer_obj was already resolved above (shared with the update path)
        allocation = CourseAllocation.objects.create(
            course_code=base_code,
            course_name=course_name,
            department=program.department,
            program=program,
            lecturer=lecturer_obj,
            number_of_students=student_count,
            program_course=pc,
            intake='normal',
            student_group=student_group,
            specialization_stem=stem,
            is_elective=is_elective,
            is_evening_weekend=is_evening
        )
        print(f"    Created allocation: {allocation.course_code} -> {student_count} students")
        return allocation, True


@transaction.atomic
def fix_management_science_allocations():
    """Main function to fix all Management Science allocations - IDEMPOTENT."""
    
    print("\n" + "="*80)
    print("FIXING MANAGEMENT SCIENCE COURSE ALLOCATIONS (V2)")
    print("="*80)
    
    # Get the Management Science department
    try:
        ms_department = Department.objects.get(name__icontains="Management Science")
        print(f"✓ Found Management Science department: {ms_department}")
    except Department.DoesNotExist:
        print("✗ Management Science department not found. Please check the name.")
        print("Available departments:")
        for dept in Department.objects.all():
            print(f"  - {dept.name}")
        return
    except Department.MultipleObjectsReturned:
        ms_department = Department.objects.filter(name__icontains="Management Science").first()
        print(f"✓ Using Management Science department: {ms_department}")
    
    # Get the BCOM program
    try:
        bcom_program = Program.objects.get(name__icontains="Bachelor of Commerce")
        print(f"✓ Found BCOM program: {bcom_program}")
    except Program.DoesNotExist:
        print("✗ BCOM program not found. Please check the name.")
        print("Available programs:")
        for prog in Program.objects.all():
            print(f"  - {prog.name}")
        return
    except Program.MultipleObjectsReturned:
        bcom_program = Program.objects.filter(name__icontains="Bachelor of Commerce").first()
        print(f"✓ Using BCOM program: {bcom_program}")
    
    # Track what we did
    changes_made = {
        'allocations_created': 0,
        'allocations_updated': 0,
        'allocations_deleted': 0,
        'groups_created': 0,
        'groups_merged': 0,
        'groups_deleted': 0,
        'categories_created': 0,
        'stems_created': 0,
        'selection_groups_created': 0,
        'errors': []
    }
    
    # ========================================================================
    # STEP 1: Get or create student groups for Y4S1
    # ========================================================================
    print("\n" + "-"*80)
    print("STEP 1: Setting up Y4S1 Student Groups")
    print("-"*80)
    
    # Y4S1 should have ONE group (all options are stems within this group)
    y4s1_group, created = safe_get_or_create_student_group(
        program=bcom_program,
        year=4,
        semester=1,
        intake="normal",
        letter="A",
        name_override="BCOM Y4S1"
    )
    if created:
        changes_made['groups_created'] += 1
        print(f"  Created Y4S1 group: {y4s1_group}")
    else:
        print(f"  Found Y4S1 group: {y4s1_group}")
    
    # Remove any other Y4S1 groups (they should be empty now)
    other_y4s1_groups = StudentGroup.objects.filter(
        program=bcom_program,
        year=4,
        semester=1,
        intake="normal"
    ).exclude(pk=y4s1_group.pk)
    
    if other_y4s1_groups.exists():
        for group in other_y4s1_groups:
            # Move any remaining allocations to main group
            for alloc in group.course_allocations.all():
                alloc.student_group = y4s1_group
                alloc.save()
                changes_made['allocations_updated'] += 1
            group.delete()
            changes_made['groups_deleted'] += 1
            print(f"  Deleted merged group: {group.name}")
    
    # ========================================================================
    # STEP 2: Get or create student groups for Y3S1
    # ========================================================================
    print("\n" + "-"*80)
    print("STEP 2: Setting up Y3S1 Student Groups")
    print("-"*80)
    
    # Y3S1 has 3 groups (A, B, C) for BCOM 331 (capacity split)
    y3s1_groups = {}
    for letter in ['A', 'B', 'C']:
        group, created = safe_get_or_create_student_group(
            program=bcom_program,
            year=3,
            semester=1,
            intake="normal",
            letter=letter,
            name_override=f"BCOM Y3S1 Group {letter}"
        )
        y3s1_groups[letter] = group
        if created:
            changes_made['groups_created'] += 1
            print(f"  Created Y3S1 group {letter}: {group}")
        else:
            print(f"  Found Y3S1 group {letter}: {group}")
    
    # ========================================================================
    # STEP 3: Create Y4S1 Specialization Stems (Options)
    # ========================================================================
    print("\n" + "-"*80)
    print("STEP 3: Creating Y4S1 Specialization Stems (Options)")
    print("-"*80)
    
    y4s1_category, created = safe_get_or_create_category(
        program=bcom_program,
        name="BCOM Y4S1 Specialization Options",
        department=ms_department,
        year=4,
        semester=1
    )
    if created:
        changes_made['categories_created'] += 1
        print(f"  Created Y4S1 category: {y4s1_category}")
    else:
        print(f"  Found Y4S1 category: {y4s1_category}")
    
    # Define Y4S1 stems (options) from the Word doc
    y4s1_stems = {
        "Accounting Option": {
            "courses": [
                {"code": "BCOM 411", "name": "Auditing I", "lecturer": "D.NYANGARA"},
                {"code": "BCOM 412", "name": "Tax Management", "lecturer": "J.MUTUA"},
                {"code": "BCOM 413", "name": "Specialized Financial Accounting Techniques", "lecturer": "J.MUMBI"}
            ],
            "students": 45,
            "description": "Accounting Option"
        },
        "Banking and Finance Option": {
            "courses": [
                {"code": "BCOM 431", "name": "Financial Management II", "lecturer": "DR. M. OLANG"},
                {"code": "BCOM 432", "name": "Management of Financial Institutions", "lecturer": "DR. G.AKENGA"},
                {"code": "BCOM 433", "name": "Financial Modeling & Forecasting", "lecturer": "DR. N.GALO"}
            ],
            "students": 100,
            "description": "Banking and Finance Option"
        },
        "Procurement Option": {
            "courses": [
                {"code": "BPLM 401", "name": "Public Relations and Customer Care", "lecturer": "DBAD"},
                {"code": "BPLM 425", "name": "Transport Economics", "lecturer": "DR. THOGORI"},
                {"code": "BPLM 411", "name": "E-Procurement", "lecturer": "W.MWANGI"}
            ],
            "students": 10,
            "description": "Procurement Option"
        },
        "Management Science Option": {
            "courses": [
                {"code": "BCOM 461", "name": "Project Management", "lecturer": "J.CHEPKONGA"},
                {"code": "BCOM 462", "name": "Operations Management", "lecturer": "K. MOENGA"},
                {"code": "BCOM 463", "name": "Business Forecasting", "lecturer": "DR. N. GALO"}
            ],
            "students": 10,
            "description": "Management Science Option"
        }
    }
    
    # Track which allocations belong to stems
    y4s1_stem_codes = []
    
    for stem_name, stem_data in y4s1_stems.items():
        stem, created = safe_get_or_create_stem(y4s1_category, stem_name)
        if created:
            changes_made['stems_created'] += 1
            print(f"    Created stem: {stem_name}")
        else:
            print(f"    Found existing stem: {stem_name}")
        
        # Process each course in the stem
        for course_info in stem_data["courses"]:
            base_code = get_base_course_code(course_info["code"])
            y4s1_stem_codes.append(base_code)
            
            # Find or create the allocation
            allocation, created = find_or_create_allocation(
                program=bcom_program,
                course_code=base_code,
                course_name=course_info["name"],
                lecturer=course_info["lecturer"],
                student_count=stem_data["students"],
                student_group=None,  # Stem courses are not tied to a specific group
                stem=stem,
                is_elective=False
            )
            
            if created:
                changes_made['allocations_created'] += 1
            else:
                changes_made['allocations_updated'] += 1
            
            # Add to stem if not already there
            if not stem.courses.filter(pk=allocation.pk).exists():
                stem.courses.add(allocation)
                print(f"      Added {base_code} to {stem_name}")
    
    # ========================================================================
    # STEP 4: Create Y4S1 Electives
    # ========================================================================
    print("\n" + "-"*80)
    print("STEP 4: Setting up Y4S1 Electives")
    print("-"*80)
    
    y4s1_electives = [
        {"code": "BCOM 416", "name": "Trust and Executorships Accounts", "lecturer": "J. MUTUA"},
        {"code": "BCOM 436", "name": "Financial Econometrics", "lecturer": "J.K. MWANGI"},
        {"code": "BCOM 467", "name": "Simulation for operations management", "lecturer": "K. O.MOENGA"},
        {"code": "BPLM 216", "name": "Purchasing Policy and Strategy", "lecturer": "L.WAWERU"}
    ]
    
    y4s1_selection_group, created = safe_get_or_create_selection_group(
        department=ms_department,
        program=bcom_program,
        name="BCOM Y4S1 Electives"
    )
    if created:
        changes_made['selection_groups_created'] += 1
        print(f"  Created Y4S1 selection group: {y4s1_selection_group}")
    else:
        print(f"  Found Y4S1 selection group: {y4s1_selection_group}")
    
    for course_info in y4s1_electives:
        base_code = get_base_course_code(course_info["code"])
        
        # Find or create the allocation
        allocation, created = find_or_create_allocation(
            program=bcom_program,
            course_code=base_code,
            course_name=course_info["name"],
            lecturer=course_info["lecturer"],
            student_count=0,  # Electives can have variable counts
            student_group=None,
            stem=None,
            is_elective=True
        )
        
        if created:
            changes_made['allocations_created'] += 1
        else:
            changes_made['allocations_updated'] += 1
        
        # Add to selection group if not already there
        if not y4s1_selection_group.courses.filter(pk=allocation.pk).exists():
            y4s1_selection_group.courses.add(allocation)
            print(f"    Added {base_code} to electives")
    
    # ========================================================================
    # STEP 5: Create Y3S1 Specialization Stems
    # ========================================================================
    print("\n" + "-"*80)
    print("STEP 5: Creating Y3S1 Specialization Stems (Options)")
    print("-"*80)
    
    # First, handle BCOM 331 - the compulsory course for ALL Y3S1 students
    print("  Setting up BCOM 331 (compulsory)...")
    
    # Find or create BCOM 331 in all 3 groups
    for letter, group in y3s1_groups.items():
        allocation, created = find_or_create_allocation(
            program=bcom_program,
            course_code="BCOM 331",
            course_name="Financial Institutions and Markets",
            lecturer="DR. G. AKENGA",
            student_count=125,  # From Word doc: 125 students per group
            student_group=group,
            stem=None,
            is_elective=False
        )
        if created:
            changes_made['allocations_created'] += 1
            print(f"    Created BCOM 331 for group {letter}")
        else:
            changes_made['allocations_updated'] += 1
            print(f"    Found BCOM 331 for group {letter}")
    
    # Create Y3S1 category
    y3s1_category, created = safe_get_or_create_category(
        program=bcom_program,
        name="BCOM Y3S1 Specialization Options",
        department=ms_department,
        year=3,
        semester=1
    )
    if created:
        changes_made['categories_created'] += 1
        print(f"  Created Y3S1 category: {y3s1_category}")
    else:
        print(f"  Found Y3S1 category: {y3s1_category}")
    
    # Define Y3S1 stems
    y3s1_stems = {
        "Banking and Finance Option": {
            "courses": [
                {"code": "BCOM 332", "name": "Corporate Finance", "lecturer": "DR. H. KIMATHI", "students": 120},
                {"code": "BCOM 333", "name": "Money and Banking", "lecturer": "D. NYANGARA", "students": 120},
                {"code": "BCOM 334", "name": "Financial Statement Analysis", "lecturer": "DR. G.AKENGA", "students": 120}
            ],
            "students": 120,
            "description": "Banking and Finance Option"
        },
        "Accounting Option": {
            "courses": [
                {"code": "BCOM 311", "name": "Advanced Financial Accounting I", "lecturer": "PROF. MASINDE", "students": 140},
                {"code": "BCOM 312", "name": "Public Sector Accounting", "lecturer": "J. MUTUA", "students": 140}
            ],
            "students": 140,
            "description": "Accounting Option"
        },
        "Procurement Option": {
            "courses": [
                {"code": "BPLM 116", "name": "Public Procurement", "lecturer": "L WAWERU", "students": 20},
                {"code": "BPLM 324", "name": "Logistics Management", "lecturer": "L. WAWERU", "students": 20},
                {"code": "BPLM 414", "name": "Supply Chain Management", "lecturer": "W. MWANGI", "students": 20}
            ],
            "students": 20,
            "description": "Procurement Option"
        },
        "Management Science Option": {
            "courses": [
                {"code": "BCOM 361", "name": "Operations Research I I", "lecturer": "K. MOENGA", "students": 20},
                {"code": "BCOM 362", "name": "Materials Management", "lecturer": "W.MWANGI", "students": 20},
                {"code": "COSC 260", "name": "Data Communication", "lecturer": "COMP", "students": 20}
            ],
            "students": 20,
            "description": "Management Science Option"
        }
    }
    
    y3s1_stem_codes = []
    
    for stem_name, stem_data in y3s1_stems.items():
        stem, created = safe_get_or_create_stem(y3s1_category, stem_name)
        if created:
            changes_made['stems_created'] += 1
            print(f"    Created stem: {stem_name}")
        else:
            print(f"    Found existing stem: {stem_name}")
        
        # Process each course in the stem
        for course_info in stem_data["courses"]:
            base_code = get_base_course_code(course_info["code"])
            y3s1_stem_codes.append(base_code)
            
            # Find or create the allocation
            allocation, created = find_or_create_allocation(
                program=bcom_program,
                course_code=base_code,
                course_name=course_info["name"],
                lecturer=course_info["lecturer"],
                student_count=stem_data["students"],
                student_group=None,  # Stem courses are not tied to a specific group
                stem=stem,
                is_elective=False
            )
            
            if created:
                changes_made['allocations_created'] += 1
            else:
                changes_made['allocations_updated'] += 1
            
            # Add to stem if not already there
            if not stem.courses.filter(pk=allocation.pk).exists():
                stem.courses.add(allocation)
                print(f"      Added {base_code} to {stem_name}")
    
    # ========================================================================
    # STEP 6: Clean up orphaned allocations and groups
    # ========================================================================
    print("\n" + "-"*80)
    print("STEP 6: Cleaning up orphaned allocations")
    print("-"*80)
    
    # Find any Y4S1 allocations that should be moved to the main group
    # (these would be non-stem, non-elective courses that somehow ended up in Y4S1)
    y4s1_pattern = re.compile(r'^BCOM (4[0-9][0-9]|BPLM 4[0-9][0-9])')
    y4s1_orphans = CourseAllocation.objects.filter(
        program=bcom_program,
        intake="normal"
    ).filter(
        Q(student_group__year=4) | Q(student_group__isnull=True)
    ).exclude(
        course_code__in=y4s1_stem_codes
    ).exclude(
        course_code__in=[c["code"] for c in y4s1_electives]
    )
    
    # Filter by pattern to avoid touching unrelated courses
    y4s1_orphans = [a for a in y4s1_orphans if y4s1_pattern.match(a.course_code)]
    
    if y4s1_orphans:
        print(f"  Found {len(y4s1_orphans)} orphan Y4S1 allocations:")
        for alloc in y4s1_orphans:
            print(f"    - {alloc.course_code}: {alloc.course_name}")
            # Set to the main Y4S1 group
            alloc.student_group = y4s1_group
            alloc.save()
            changes_made['allocations_updated'] += 1
            print(f"      Moved to {y4s1_group.name}")
    
    # ========================================================================
    # STEP 7: SUMMARY
    # ========================================================================
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    print(f"\nChanges made:")
    print(f"  - Allocations created: {changes_made.get('allocations_created', 0)}")
    print(f"  - Allocations updated: {changes_made.get('allocations_updated', 0)}")
    print(f"  - Allocations deleted (duplicates): {changes_made.get('allocations_deleted', 0)}")
    print(f"  - Student groups created: {changes_made.get('groups_created', 0)}")
    print(f"  - Student groups merged: {changes_made.get('groups_merged', 0)}")
    print(f"  - Student groups deleted: {changes_made.get('groups_deleted', 0)}")
    print(f"  - Specialization categories created: {changes_made.get('categories_created', 0)}")
    print(f"  - Specialization stems created: {changes_made.get('stems_created', 0)}")
    print(f"  - Selection groups created: {changes_made.get('selection_groups_created', 0)}")
    
    if changes_made['errors']:
        print(f"\nErrors encountered ({len(changes_made['errors'])}):")
        for error in changes_made['errors']:
            print(f"  - {error}")
    
    print("\nY4S1 Structure:")
    print(f"  - Student Group: {y4s1_group}")
    print("  - Stems:")
    for stem in y4s1_category.stems.all():
        courses = [c.course_code for c in stem.courses.all()]
        total_students = sum(c.number_of_students for c in stem.courses.all())
        print(f"      {stem.name}: {', '.join(courses) if courses else 'No courses'} ({total_students} students)")
    
    print(f"  - Electives:")
    elective_codes = [c.course_code for c in y4s1_selection_group.courses.all()]
    print(f"      {', '.join(elective_codes) if elective_codes else 'None'}")
    
    print("\nY3S1 Structure:")
    print("  - Compulsory Course (split across groups): BCOM 331 (125 students x 3 groups)")
    for letter, group in y3s1_groups.items():
        bcom331_count = group.course_allocations.filter(course_code="BCOM 331").count()
        print(f"      Group {letter}: {bcom331_count} allocations")
    
    print("  - Stems:")
    for stem in y3s1_category.stems.all():
        courses = [c.course_code for c in stem.courses.all()]
        total_students = sum(c.number_of_students for c in stem.courses.all())
        print(f"      {stem.name}: {', '.join(courses) if courses else 'No courses'} ({total_students} students)")
    
    print("\n" + "="*80)
    print("FIX COMPLETED SUCCESSFULLY")
    print("="*80)
    print("\nThis script is idempotent - you can run it again safely.")


try:
    fix_management_science_allocations()
except Exception as e:
    print(f"\nERROR: {str(e)}")
    import traceback
    traceback.print_exc()