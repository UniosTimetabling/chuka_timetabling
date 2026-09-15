from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('special_requests', '0002_specialrequestallocation'),
    ]

    operations = [
        migrations.AddField(
            model_name='specialrequest',
            name='constraint_applied_type',
            field=models.CharField(
                blank=True, default='', max_length=30,
                choices=[
                    ('lecturer_block', 'Lecturer Blocked Days/Times'),
                    ('lecturer_time_pref', 'Lecturer Day/Time Preference'),
                    ('lecturer_venue_pref', 'Lecturer Venue Preference'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='specialrequest',
            name='constraint_applied_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='specialrequest',
            name='constraint_applied_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
