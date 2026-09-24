# Generated manually — mirrors migration 0002 (TempTimetable fix), applied
# to the exam timetable models so the same course can never be persisted
# twice for two different exam dates/venues, and so merged exam families
# (multiple different courses sharing one venue/slot) stop silently losing
# rows to the old, too-narrow unique constraint.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('course_allocation', '0013_combinedcoursegroup_origin_department_and_more'),
        ('room_management', '0003_alter_venueblock_id'),
        ('timetable', '0003_lecturertimepreference_lecturervenuepreference_and_more'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='examtemptimetable',
            unique_together={('course_allocation', 'venue', 'date', 'start_time', 'end_time')},
        ),
        migrations.AlterUniqueTogether(
            name='examtimetable',
            unique_together={('course_allocation', 'venue', 'date', 'start_time', 'end_time')},
        ),
    ]
