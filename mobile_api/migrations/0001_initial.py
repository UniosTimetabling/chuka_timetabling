from django.db import migrations, models
import django.db.models.deletion
import mobile_api.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("program_management", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Announcement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "announcement_type",
                    models.CharField(
                        choices=[("event", "Event"), ("memo", "Memo")],
                        default="memo",
                        max_length=10,
                    ),
                ),
                (
                    "audience",
                    models.CharField(
                        choices=[
                            ("all", "Everyone"),
                            ("students", "Students only"),
                            ("lecturers", "Lecturers only"),
                        ],
                        default="all",
                        max_length=10,
                    ),
                ),
                (
                    "year",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        help_text="Leave blank to reach every year of study in the selected program.",
                        null=True,
                    ),
                ),
                ("date", models.DateTimeField(help_text="Date shown to the reader (event date, or memo issue date).")),
                ("is_published", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "program",
                    models.ForeignKey(
                        blank=True,
                        help_text="Leave blank to reach students in every program.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mobile_announcements",
                        to="program_management.program",
                    ),
                ),
            ],
            options={
                "ordering": ["-date"],
            },
        ),
        migrations.CreateModel(
            name="AnnouncementAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to=mobile_api.models.announcement_attachment_path)),
                ("name", models.CharField(blank=True, max_length=255)),
                ("mime_type", models.CharField(blank=True, max_length=100)),
                (
                    "announcement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="mobile_api.announcement",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="announcement",
            index=models.Index(fields=["is_published", "audience"], name="mobile_api__is_publ_5c3a1a_idx"),
        ),
        migrations.AddIndex(
            model_name="announcement",
            index=models.Index(fields=["program", "year"], name="mobile_api__program_9d2b4e_idx"),
        ),
    ]
