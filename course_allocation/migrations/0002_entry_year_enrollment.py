import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def _parse_academic_year_start(label):
    """'2024/2025' -> 2024. Anything else (blank, malformed) -> None."""
    if not label:
        return None
    try:
        return int(str(label).split("/")[0].strip())
    except (ValueError, IndexError):
        return None


def populate_entry_year(apps, schema_editor):
    """
    Best-effort conversion of the old (year-of-study, semester, academic_year
    label) rows into the new (entry_year) shape.

    For each old row:
      - if academic_year parses to a start year, entry_year = that start year
        - (year_of_study - 1)
      - otherwise fall back to: today's year - (year_of_study - 1), i.e.
        assume the record was current as of the migration date.

    Old rows saved BOTH a semester=1 and semester=2 row per (program, year).
    Both collapse onto the same entry_year, so duplicates are merged --
    keeping the higher student count (safer than silently dropping data).
    """
    ProgramEnrollment = apps.get_model("course_allocation", "ProgramEnrollment")
    this_year = timezone.now().year

    merged = {}  # (program_id, entry_year) -> number_of_students
    for row in ProgramEnrollment.objects.all():
        start = _parse_academic_year_start(getattr(row, "academic_year", "") or "")
        if start is None:
            start = this_year
        entry_year = start - (row.year - 1)
        key = (row.program_id, entry_year)
        merged[key] = max(merged.get(key, 0), row.number_of_students)

    ProgramEnrollment.objects.all().delete()
    for (program_id, entry_year), students in merged.items():
        ProgramEnrollment.objects.create(
            program_id=program_id,
            entry_year=entry_year,
            number_of_students=students,
        )


def noop_reverse(apps, schema_editor):
    # Not reversible in a meaningful way -- old (year, semester, academic_year)
    # split can't be reconstructed from entry_year alone.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AcademicYearTracker",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("current_year", models.PositiveIntegerField(
                    help_text="Calendar year that currently counts as 'Year 1' of study "
                              "for every program. Advance this once per academic year "
                              "instead of editing individual enrollment records.",
                )),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Academic Year Tracker",
                "verbose_name_plural": "Academic Year Tracker",
            },
        ),

        # 1. Add entry_year as nullable so we can populate it before making it
        #    the real key.
        migrations.AddField(
            model_name="programenrollment",
            name="entry_year",
            field=models.PositiveIntegerField(null=True),
        ),

        # 2. Drop the old uniqueness constraint (it references fields we're
        #    about to remove).
        migrations.AlterUniqueTogether(
            name="programenrollment",
            unique_together=set(),
        ),

        # 3. Convert + de-duplicate data.
        migrations.RunPython(populate_entry_year, noop_reverse),

        # 4. Remove the old fields.
        migrations.RemoveField(model_name="programenrollment", name="year"),
        migrations.RemoveField(model_name="programenrollment", name="semester"),
        migrations.RemoveField(model_name="programenrollment", name="academic_year"),

        # 5. Make entry_year required + re-add uniqueness + default ordering.
        migrations.AlterField(
            model_name="programenrollment",
            name="entry_year",
            field=models.PositiveIntegerField(help_text="Calendar year this cohort was admitted, e.g. 2023."),
        ),
        migrations.AlterUniqueTogether(
            name="programenrollment",
            unique_together={("program", "entry_year")},
        ),
        migrations.AlterModelOptions(
            name="programenrollment",
            options={
                "ordering": ("program__name", "-entry_year"),
                "verbose_name": "Program Enrollment",
                "verbose_name_plural": "Program Enrollments",
            },
        ),
    ]
