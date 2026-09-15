from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0014_allocation_set"),
    ]

    operations = [
        # ── SelectionGroup ───────────────────────────────────────────────
        migrations.AddField(
            model_name="selectiongroup",
            name="allocation_set",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="selection_groups",
                to="course_allocation.allocationset",
            ),
        ),

        # ── SpecializationCategory ───────────────────────────────────────
        migrations.AddField(
            model_name="specializationcategory",
            name="allocation_set",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="specialization_categories",
                to="course_allocation.allocationset",
            ),
        ),
        # Old constraint was ("program", "name") — blocked reusing a
        # category name for the same program across different semesters/
        # allocation sets forever. New constraint adds allocation_set so
        # the same name is only unique WITHIN one set.
        migrations.AlterUniqueTogether(
            name="specializationcategory",
            unique_together={("program", "name", "allocation_set")},
        ),

        # ── CombinedCourseGroup ──────────────────────────────────────────
        migrations.AddField(
            model_name="combinedcoursegroup",
            name="allocation_set",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="combined_groups",
                to="course_allocation.allocationset",
            ),
        ),
        # group_code was globally unique=True across the ENTIRE system —
        # once "COSC 471" was combined once, no department could ever
        # reuse that code again, in any future semester. Now unique per
        # allocation_set instead.
        migrations.AlterField(
            model_name="combinedcoursegroup",
            name="group_code",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterUniqueTogether(
            name="combinedcoursegroup",
            unique_together={("group_code", "allocation_set")},
        ),
    ]
