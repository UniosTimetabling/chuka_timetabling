from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("mobile_api", "0002_rename_mobile_api__is_publ_5c3a1a_idx_mobile_api__is_publ_ed8883_idx_and_more"),
        ("course_allocation", "0013_combinedcoursegroup_origin_department_and_more"),
        ("lecturer_portal", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonalCourseEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[("student", "Student"), ("lecturer", "Lecturer")],
                        max_length=10,
                    ),
                ),
                ("reg_no", models.CharField(blank=True, default="", max_length=40)),
                (
                    "course_allocation",
                    models.ForeignKey(
                        help_text=(
                            "The allocation the search matched. If it belongs to a "
                            "CombinedCourseGroup this is always the group's primary "
                            "allocation — course_search.py resolves to that, mirroring "
                            "timetable/find_courses.py's collapse-by-group rule — so the "
                            "group's one shared timetable slot is what shows up, and "
                            "adding any of its sections is the same action as adding the "
                            "group itself."
                        ),
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="personal_timetable_entries",
                        to="course_allocation.courseallocation",
                    ),
                ),
                (
                    "lecturer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="personal_timetable_entries",
                        to="lecturer_portal.lecturer",
                    ),
                ),
                ("added_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Personal Timetable Entry",
                "verbose_name_plural": "Personal Timetable Entries",
                "ordering": ["-added_at"],
            },
        ),
        migrations.AddIndex(
            model_name="personalcourseentry",
            index=models.Index(fields=["role", "reg_no"], name="mobile_api__role_c47b57_idx"),
        ),
        migrations.AddIndex(
            model_name="personalcourseentry",
            index=models.Index(fields=["role", "lecturer"], name="mobile_api__role_b19f4a_idx"),
        ),
    ]
