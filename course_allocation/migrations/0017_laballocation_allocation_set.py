from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0016_archivedcourseallocation_allocation_set"),
    ]

    operations = [
        migrations.AddField(
            model_name="laballocation",
            name="allocation_set",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lab_allocations",
                to="course_allocation.allocationset",
                help_text=(
                    "Which concurrent AllocationSet (e.g. Semester 1 vs "
                    "Semester 2, or a Special allocation) this lab/workshop "
                    "allocation belongs to. Mirrors CourseAllocation.allocation_set. "
                    "Null only for rows created before this field existed; "
                    "the backfill command attaches those to the department's "
                    "legacy set."
                ),
            ),
        ),
    ]
