from django.db import migrations, models
import django.db.models.deletion
import export_import.models


class Migration(migrations.Migration):

    dependencies = [
        ('export_import', '0002_pdf_watermark_and_signatories'),
        ('department_management', '0001_initial'),
        ('program_management', '0006_rename_program_man_status_e5b1a1_idx_program_man_status_31190c_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='GeneratedTimetablePDF',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField()),
                ('timetable_type', models.CharField(choices=[('class', 'Class Timetable'), ('exam', 'Exam Timetable')], max_length=10)),
                ('pdf_file', models.FileField(blank=True, null=True, upload_to=export_import.models.program_year_pdf_upload_path)),
                ('is_ready', models.BooleanField(default=False, help_text='False while a (re)generation is in progress — the serving view will not hand out a not-yet-written file.')),
                ('row_count', models.PositiveIntegerField(default=0, help_text='Number of scheduled entries in the last successful build.')),
                ('content_hash', models.CharField(blank=True, default='', help_text='Hash of the source rows used for this build, so an unchanged scope can be skipped during a full regeneration sweep.', max_length=64)),
                ('generated_at', models.DateTimeField(blank=True, null=True)),
                ('error_message', models.TextField(blank=True, default='')),
                ('department', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='generated_timetable_pdfs', to='department_management.department')),
                ('program', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='generated_timetable_pdfs', to='program_management.program')),
            ],
            options={
                'verbose_name': 'Generated Timetable PDF',
                'verbose_name_plural': 'Generated Timetable PDFs',
            },
        ),
        migrations.AddIndex(
            model_name='generatedtimetablepdf',
            index=models.Index(fields=['department', 'program', 'year', 'timetable_type'], name='export_impo_departm_6da7ff_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='generatedtimetablepdf',
            unique_together={('department', 'program', 'year', 'timetable_type')},
        ),
    ]
