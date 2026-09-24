from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("timetable", "0007_examtimetable_allocated_students_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="TimetableIssue",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "issue_type",
                    models.CharField(
                        choices=[
                            ("capacity_over", "Over venue capacity"),
                            ("capacity_under", "Venue much bigger than class"),
                            ("venue_collision", "Venue double-booked"),
                            ("lecturer_collision", "Lecturer double-booked"),
                            ("program_collision", "Program/year overlap"),
                            ("other", "Other override"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "severity",
                    models.CharField(
                        choices=[("warning", "Warning"), ("error", "Error (forced through)")],
                        default="warning",
                        max_length=16,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("autoscheduler", "Autoscheduler"),
                            ("manual_override", "Manual override"),
                            ("system", "System"),
                        ],
                        default="system",
                        max_length=32,
                    ),
                ),
                ("message", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "timetable_entry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="issues",
                        to="timetable.timetable",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="timetableissue",
            index=models.Index(fields=["timetable_entry"], name="tt_issue_entry_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="timetableissue",
            unique_together={("timetable_entry", "issue_type")},
        ),
    ]
