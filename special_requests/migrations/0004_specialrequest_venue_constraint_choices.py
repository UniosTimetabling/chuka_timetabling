from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('special_requests', '0003_specialrequest_constraint_applied'),
    ]

    operations = [
        migrations.AlterField(
            model_name='specialrequest',
            name='constraint_applied_type',
            field=models.CharField(
                blank=True, default='', max_length=30,
                choices=[
                    ('lecturer_block', 'Lecturer Blocked Days/Times'),
                    ('lecturer_time_pref', 'Lecturer Day/Time Preference'),
                    ('lecturer_venue_pref', 'Lecturer Venue Preference'),
                    ('venue_block', 'Blocked Venue'),
                    ('venue_specialization', 'Venue Specialization (priority pass)'),
                    ('venue_exclusive', 'Exclusive Venue Restriction'),
                ],
            ),
        ),
    ]
