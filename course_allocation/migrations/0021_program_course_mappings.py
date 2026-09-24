# Hand-written: map curriculum (ProgramCourse) courses to combination stems and
# elective groups in advance, so allocations created later can be attached to
# them automatically.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_allocation', '0020_sections_and_nested_elective_groups'),
        ('program_management', '0007_alter_programcourse_semester'),
    ]

    operations = [
        migrations.AddField(
            model_name='specializationstem',
            name='program_courses',
            field=models.ManyToManyField(
                blank=True, related_name='mapped_stems',
                to='program_management.programcourse',
                help_text=(
                    "Curriculum (course-master) courses mapped to this stem IN ADVANCE, "
                    "on the Student Groups / Stems / Electives page. When a "
                    "CourseAllocation is later created for one of these ProgramCourses "
                    "(same allocation set), it is attached to this stem automatically "
                    "(see course_allocation/course_mapping.py)."
                ),
            ),
        ),
        migrations.AddField(
            model_name='selectiongroup',
            name='program_courses',
            field=models.ManyToManyField(
                blank=True, related_name='mapped_selection_groups',
                to='program_management.programcourse',
                help_text=(
                    "Curriculum (course-master) courses mapped to this elective group "
                    "IN ADVANCE, on the Student Groups / Stems / Electives page. When a "
                    "CourseAllocation is later created for one of these ProgramCourses "
                    "(same allocation set), it is attached to this group automatically "
                    "(see course_allocation/course_mapping.py)."
                ),
            ),
        ),
    ]
