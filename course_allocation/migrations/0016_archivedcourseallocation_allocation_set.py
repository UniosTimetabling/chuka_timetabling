from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0015_group_allocation_sets"),
    ]

    operations = [
        migrations.AddField(
            model_name="archivedcourseallocation",
            name="allocation_set",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="archived_allocations",
                to="course_allocation.allocationset",
                help_text=(
                    "Which concurrent AllocationSet this archive came from. "
                    "Without this, archives from different concurrent sets "
                    "that happen to share the same semester label got "
                    "merged together on restore/delete. Null only for "
                    "archives created before this field existed."
                ),
            ),
        ),
    ]
