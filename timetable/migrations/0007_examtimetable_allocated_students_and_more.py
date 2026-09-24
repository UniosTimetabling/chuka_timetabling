from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("timetable", "0006_alter_analysisaccessshare_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="examtimetable",
            name="allocated_students",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text=(
                    "How many of course_allocation's students sit THIS venue "
                    "for this entry. Only meaningfully different from the "
                    "course's total enrollment when the exam is split across "
                    "more than one venue for the same date/slot — one row "
                    "per venue, each with its own share. Null on legacy rows "
                    "written before this field existed; reports should fall "
                    "back to the course's full enrollment in that case."
                ),
            ),
        ),
        migrations.AddField(
            model_name="examtemptimetable",
            name="allocated_students",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text=(
                    "How many of course_allocation's students sit THIS venue "
                    "for this entry — see ExamTimetable.allocated_students "
                    "for the full explanation. Copied across on publish."
                ),
            ),
        ),
    ]
