# Run with: python manage.py shell < diagnose_collision_exempt.py
# (or paste the body into `python manage.py shell`)
#
# Finds the two CourseAllocation rows for BCOM 351-A and AGED 314C in the
# Bachelor of Agribusiness Management Year 3 cohort, and prints every
# field that exam_is_collision_exempt() consults, so we can see exactly
# which of its 8 rules is (wrongly) exempting them from the conflict check.

from course_allocation.models import CourseAllocation  # adjust import path if different

def describe(alloc):
    stem = getattr(alloc, "specialization_stem", None)
    print(f"  id={alloc.id}  code={alloc.course_code!r}")
    print(f"    program={getattr(alloc.program, 'name', None)!r} "
          f"(id={getattr(alloc.program, 'id', None)})")
    print(f"    specialization_stem_id={getattr(alloc, 'specialization_stem_id', None)} "
          f"category_id={getattr(stem, 'category_id', None) if stem else None}")
    print(f"    student_group_id={getattr(alloc, 'student_group_id', None)}")
    print(f"    selection_group_id={getattr(alloc, 'selection_group_id', None)}")
    print(f"    special_intake_group_id={getattr(alloc, 'special_intake_group_id', None)}")

qs = CourseAllocation.objects.filter(
    program__name__icontains="Agribusiness Management",
    course_code__in=["BCOM 351-A", "AGED 314C"],
)
rows = list(qs.select_related("program", "specialization_stem", "specialization_stem__category"))
print(f"Found {len(rows)} matching allocation(s):")
for r in rows:
    describe(r)

# Also check CombinedCourseGroup pairing directly, since that's rule 8
# and isn't visible on the CourseAllocation record itself.
try:
    from course_allocation.models import CombinedCourseGroup
    ids = [r.id for r in rows]
    groups = CombinedCourseGroup.objects.filter(allocations__id__in=ids).distinct()
    print(f"\nCombinedCourseGroup rows referencing either allocation: {groups.count()}")
    for g in groups:
        print(f"  group={g.group_code!r} allocations="
              f"{list(g.allocations.values_list('id', 'course_code'))}")
except Exception as exc:
    print(f"(CombinedCourseGroup check skipped: {exc})")
