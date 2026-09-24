from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("department_management", "0001_initial"),  # adjust if your latest dept migration differs
        ("program_management", "0006_rename_program_man_status_e5b1a1_idx_program_man_status_31190c_idx_and_more"),
        ("course_allocation", "0013_combinedcoursegroup_origin_department_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AllocationSet",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("academic_year", models.CharField(blank=True, default="", max_length=20)),
                ("allocation_type", models.CharField(choices=[("auto_full", "Auto-allocate (full semester composition)"), ("selective", "Selective (hand-picked courses)")], default="auto_full", max_length=20)),
                ("is_special", models.BooleanField(default=False)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted_tt", "Submitted to Timetabling Office"), ("submitted_dvc", "Submitted to DVC")], db_index=True, default="draft", max_length=20)),
                ("submitted_to_tt_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_to_dvc_at", models.DateTimeField(blank=True, null=True)),
                ("is_legacy", models.BooleanField(default=False)),
                ("is_archived", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="allocation_sets_created", to=settings.AUTH_USER_MODEL)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="allocation_sets", to="department_management.department")),
            ],
            options={
                "verbose_name": "Allocation Set",
                "verbose_name_plural": "Allocation Sets",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="AllocationSetSemesterComponent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("semester_number", models.PositiveSmallIntegerField()),
                ("scope", models.CharField(choices=[("all", "All programs / all courses"), ("selected", "Selected programs/courses only")], default="all", max_length=10)),
                ("added_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("allocation_set", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="semester_components", to="course_allocation.allocationset")),
                ("programs", models.ManyToManyField(blank=True, related_name="allocation_set_components", to="program_management.program")),
            ],
            options={
                "ordering": ["allocation_set", "semester_number"],
            },
        ),
        migrations.CreateModel(
            name="AllocationSetComponentCourse",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("component", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pinned_courses", to="course_allocation.allocationsetsemestercomponent")),
                ("program_course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="program_management.programcourse")),
            ],
        ),
        migrations.AlterUniqueTogether(
            name="allocationsetsemestercomponent",
            unique_together={("allocation_set", "semester_number")},
        ),
        migrations.AlterUniqueTogether(
            name="allocationsetcomponentcourse",
            unique_together={("component", "program_course")},
        ),
        migrations.AddIndex(
            model_name="allocationset",
            index=models.Index(fields=["department", "status"], name="course_allo_departm_a1b2c3_idx"),
        ),
        migrations.AddIndex(
            model_name="allocationset",
            index=models.Index(fields=["department", "is_archived"], name="course_allo_departm_d4e5f6_idx"),
        ),
        # ── The only touch to an existing table: one new, NULLABLE column. ──
        # No default value is forced onto existing rows, nothing is renamed,
        # nothing is dropped. Existing CourseAllocation rows are left exactly
        # as they are (allocation_set = NULL) until the backfill command runs.
        migrations.AddField(
            model_name="courseallocation",
            name="allocation_set",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="course_allocations",
                to="course_allocation.allocationset",
            ),
        ),
    ]
