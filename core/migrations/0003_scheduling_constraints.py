from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_orgrole_department_faculty_scope'),
        ('lecturer_portal', '0001_initial'),
        ('room_management', '0002_venuespecialization_exclusive_venueblock'),
    ]

    operations = [
        migrations.CreateModel(
            name='SchedulerConstraintToggle',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.SlugField(
                    max_length=60, unique=True,
                    help_text="Stable identifier used in code, e.g. 'venue_blocks', 'lecturer_blocked_slots'.",
                )),
                ('label', models.CharField(max_length=150)),
                ('description', models.TextField(blank=True, default='')),
                ('applies_to', models.CharField(
                    choices=[
                        ('regular', 'Regular timetable autoscheduler only'),
                        ('exam', 'Exam autoscheduler only'),
                        ('both', 'Both regular and exam autoschedulers'),
                    ],
                    default='regular', max_length=10,
                )),
                ('is_enabled', models.BooleanField(
                    default=True,
                    help_text=(
                        "Persisted default for this constraint category. Can still be "
                        "switched off for a single run from the autoscheduler's pre-run "
                        "confirmation screen without changing this saved default."
                    ),
                )),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Scheduler Constraint Toggle',
                'verbose_name_plural': 'Scheduler Constraint Toggles',
                'ordering': ['applies_to', 'key'],
            },
        ),
        migrations.CreateModel(
            name='LecturerBlockedSlot',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.CharField(choices=[
                    ('Monday', 'Monday'), ('Tuesday', 'Tuesday'), ('Wednesday', 'Wednesday'),
                    ('Thursday', 'Thursday'), ('Friday', 'Friday'),
                    ('Saturday', 'Saturday'), ('Sunday', 'Sunday'),
                ], max_length=20)),
                ('start_time', models.TimeField(
                    blank=True, null=True,
                    help_text='Leave blank together with End Time to block the entire day.',
                )),
                ('end_time', models.TimeField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('lecturer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='blocked_slots', to='lecturer_portal.lecturer',
                )),
            ],
            options={
                'verbose_name': 'Lecturer Blocked Slot',
                'verbose_name_plural': 'Lecturer Blocked Slots',
                'ordering': ['lecturer__name', 'day', 'start_time'],
            },
        ),
        migrations.CreateModel(
            name='LecturerTimePreference',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.CharField(choices=[
                    ('Monday', 'Monday'), ('Tuesday', 'Tuesday'), ('Wednesday', 'Wednesday'),
                    ('Thursday', 'Thursday'), ('Friday', 'Friday'),
                    ('Saturday', 'Saturday'), ('Sunday', 'Sunday'),
                ], max_length=20)),
                ('start_time', models.TimeField(
                    blank=True, null=True,
                    help_text="Leave blank together with End Time to mean 'any time on this day'.",
                )),
                ('end_time', models.TimeField(blank=True, null=True)),
                ('notes', models.CharField(blank=True, default='', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('lecturer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='time_preferences', to='lecturer_portal.lecturer',
                )),
            ],
            options={
                'verbose_name': 'Lecturer Time Preference',
                'verbose_name_plural': 'Lecturer Time Preferences',
                'ordering': ['lecturer__name', 'day', 'start_time'],
            },
        ),
        migrations.CreateModel(
            name='LecturerVenuePreference',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('notes', models.CharField(blank=True, default='', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('lecturer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='venue_preferences', to='lecturer_portal.lecturer',
                )),
                ('venues', models.ManyToManyField(
                    related_name='lecturer_preferences', to='room_management.venue',
                )),
            ],
            options={
                'verbose_name': 'Lecturer Venue Preference',
                'verbose_name_plural': 'Lecturer Venue Preferences',
                'ordering': ['lecturer__name'],
            },
        ),
    ]
