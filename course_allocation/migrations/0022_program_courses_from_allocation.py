from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("program_management", "0007_alter_programcourse_semester"),
        ("course_allocation", "0021_program_course_mappings"),
    ]

    operations = [
        migrations.AddField(
            model_name="selectiongroup",
            name="program_courses_from_allocation",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Subset of program_courses that were mapped automatically by the "
                    "map_program_courses_from_existing_allocations.py backfill script "
                    "(reverse-derived from an existing CourseAllocation already "
                    "attached here), rather than picked by hand in the Map courses "
                    "dialog. Used only to flag those rows in the UI."
                ),
                related_name="+",
                to="program_management.programcourse",
            ),
        ),
        migrations.AddField(
            model_name="specializationstem",
            name="program_courses_from_allocation",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Subset of program_courses that were mapped automatically by the "
                    "map_program_courses_from_existing_allocations.py backfill script "
                    "(reverse-derived from an existing CourseAllocation already "
                    "attached here), rather than picked by hand in the Map courses "
                    "dialog. Used only to flag those rows in the UI."
                ),
                related_name="+",
                to="program_management.programcourse",
            ),
        ),
    ]
