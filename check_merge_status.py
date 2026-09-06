"""
Run this with: python manage.py shell < check_merge_status.py
(from inside your Django project directory, e.g. chuka_timetabling/)

For each lecturer-conflict pair reported, checks whether the two
allocations are registered in the SAME CombinedCourseGroup. If NONE of
them are, the report is correct as-is (real conflicts) and the fix
needed is in SiblingColocation/merging, not the report layer.
"""
from course_allocation.models import CourseAllocation, CombinedCourseGroup

pairs_to_check = [
    ("ENGL 482", "ENGL 352"),
    ("SOCI 101(D)", "SOCI 101"),
    ("CHEM 323", "CHEM 322"),
    ("MATH 222", "MATH 222-COMP"),
    ("KISW 102-B", "KISW 102-C"),
]

for code_a, code_b in pairs_to_check:
    a = CourseAllocation.objects.filter(course_code=code_a).first()
    b = CourseAllocation.objects.filter(course_code=code_b).first()
    if not a or not b:
        print(f"{code_a} / {code_b}: could not find one or both allocations")
        continue
    groups_a = set(CombinedCourseGroup.objects.filter(allocations=a).values_list('id', flat=True))
    groups_b = set(CombinedCourseGroup.objects.filter(allocations=b).values_list('id', flat=True))
    shared = groups_a & groups_b
    print(f"{code_a} (groups={groups_a}) / {code_b} (groups={groups_b}) -> shared={bool(shared)}")
