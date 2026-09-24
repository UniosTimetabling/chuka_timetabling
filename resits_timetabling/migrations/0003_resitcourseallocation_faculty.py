from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('faculty_management', '0001_initial'),
        ('resits_timetabling', '0002_resitvenueexclusion_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='resitcourseallocation',
            name='faculty',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='resit_allocations',
                to='faculty_management.faculty',
                help_text=(
                    "Faculty this resit course belongs to. Auto-derived from the "
                    "department when not supplied explicitly (e.g. via an imported "
                    "'faculty' column). Used by the auto-scheduler to disperse "
                    "courses per faculty across days, timeslots and venues."
                ),
            ),
        ),
    ]
