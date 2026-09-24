import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("course_allocation", "0022_program_courses_from_allocation"),
    ]

    operations = [
        migrations.CreateModel(
            name="GroupingTemplateStemAssignment",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_grouping_template_stem_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stem_assignment",
                        to="course_allocation.groupingtemplategroup",
                    ),
                ),
                (
                    "stem",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grouping_template_assignments",
                        to="course_allocation.specializationstem",
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stem_assignments",
                        to="course_allocation.groupingtemplate",
                    ),
                ),
            ],
            options={
                "verbose_name": "Grouping Template Stem Assignment",
                "verbose_name_plural": "Grouping Template Stem Assignments",
                "ordering": ["template", "stem", "group"],
                "unique_together": {("template", "group")},
            },
        ),
    ]
