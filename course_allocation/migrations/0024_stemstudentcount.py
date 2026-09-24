import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("course_allocation", "0023_groupingtemplatestemassignment"),
    ]

    operations = [
        migrations.CreateModel(
            name="StemStudentCount",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("year", models.PositiveSmallIntegerField(help_text="The program year this headcount applies to.")),
                (
                    "semester",
                    models.PositiveSmallIntegerField(
                        choices=[(1, "Semester 1"), (2, "Semester 2")],
                        help_text="The semester this headcount applies to.",
                    ),
                ),
                ("number_of_students", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "stem",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_counts",
                        to="course_allocation.specializationstem",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+", to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Combination Stem Student Count",
                "verbose_name_plural": "Combination Stem Student Counts",
                "ordering": ("stem__category__program__name", "-year", "semester"),
                "unique_together": {("stem", "year", "semester")},
            },
        ),
    ]
