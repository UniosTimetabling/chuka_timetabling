# Generated manually — adds AnalysisAccessShare, the single global on/off
# switch (plus optional per-department scoping) that lets a Timetable
# Admin/Director/SUDO share read-only access to /timetable/analysis/ with
# COD and/or COD Admin accounts. See timetable/models.py for the full
# docstring and timetable/analysis_reports.py (_can_access_analysis,
# analysis_access_required) for how it's enforced.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('department_management', '0003_backfill_department_codes'),
        ('timetable', '0004_alter_examtemptimetable_unique_together_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AnalysisAccessShare',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(
                    default=False,
                    help_text="Master switch. Off = only SUDO/Director/Timetable Admin can open "
                              "/timetable/analysis/, exactly as before this feature existed.",
                )),
                ('share_with_cod', models.BooleanField(
                    default=False,
                    help_text="Let COD accounts open the analysis dashboard (always scoped to "
                              "their own department only).",
                )),
                ('share_with_cod_admin', models.BooleanField(
                    default=False,
                    help_text="Let COD Admin accounts open the analysis dashboard (always scoped "
                              "to their own department only).",
                )),
                ('apply_to_all_departments', models.BooleanField(
                    default=True,
                    help_text="On = every department's COD/COD Admin (per the two switches above) "
                              "gets access. Off = only the departments picked below do.",
                )),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('departments', models.ManyToManyField(
                    blank=True,
                    help_text="Only used when 'apply to all departments' is off.",
                    related_name='analysis_access_shares',
                    to='department_management.department',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='analysis_access_shares_updated',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Analysis Dashboard Access Share',
                'verbose_name_plural': 'Analysis Dashboard Access Share',
            },
        ),
    ]
