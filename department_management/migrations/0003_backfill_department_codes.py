"""
Backfills a DepartmentCode for every Department that doesn't have one yet,
using the same default heuristic new departments get automatically (see
department_management/department_codes.py::generate_default_code). Safe to
run more than once — departments that already have a code are skipped.
"""
from django.db import migrations


def backfill_codes(apps, schema_editor):
    from department_management.department_codes import generate_default_code

    Department = apps.get_model('department_management', 'Department')
    DepartmentCode = apps.get_model('department_management', 'DepartmentCode')

    existing_codes = set(DepartmentCode.objects.values_list('code', flat=True))

    for dept in Department.objects.order_by('name'):
        if DepartmentCode.objects.filter(department=dept).exists():
            continue
        base = generate_default_code(dept.name)
        code = base
        suffix = 2
        while code in existing_codes:
            code = f"{base}{suffix}"
            suffix += 1
        DepartmentCode.objects.create(department=dept, code=code)
        existing_codes.add(code)


def noop_reverse(apps, schema_editor):
    # Codes are useful reference data even after a rollback of this data
    # migration's "operation" step — nothing to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('department_management', '0002_departmentcode'),
    ]

    operations = [
        migrations.RunPython(backfill_codes, noop_reverse),
    ]
