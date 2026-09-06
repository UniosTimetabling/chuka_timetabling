import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("program_management", "0001_initial"),
        ("course_allocation", "0002_entry_year_enrollment"),
    ]

    operations = [
        migrations.AddField(
            model_name="archivedcourseallocation",
            name="program_course",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="archived_course_allocations",
                to="program_management.programcourse",
                help_text=(
                    "The ProgramCourse (curriculum entry) this allocation was "
                    "derived from at the time of archiving. Required to restore "
                    "this record to an active CourseAllocation. Null for "
                    "archives created before this field existed — those cannot "
                    "be restored automatically."
                ),
            ),
        ),
    ]
