"""
python manage.py shell < check_math222_family.py

Checks every MATH 222 variant's CombinedCourseGroup membership to see
whether the merge setup is complete or partial.
"""
from course_allocation.models import CourseAllocation, CombinedCourseGroup

variants = ["MATH 222", "MATH 222(A)", "MATH 222(B)", "MATH 222-COMP", "MATH 222-GEO", "MATH 222-HSC"]

for code in variants:
    allocs = CourseAllocation.objects.filter(course_code=code)
    if not allocs.exists():
        print(f"{code}: NOT FOUND")
        continue
    for a in allocs:
        groups = set(CombinedCourseGroup.objects.filter(allocations=a).values_list('id', flat=True))
        print(f"{code} (id={a.id}, lecturer={getattr(a.lecturer, 'name', None)}): groups={groups}")
