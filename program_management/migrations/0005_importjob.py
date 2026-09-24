# Generated for the async Program Course CSV import redesign.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('program_management', '0004_normalize_unit_type_typos'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImportJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('resource_type', models.CharField(choices=[('program_course', 'Program Course')], db_index=True, default='program_course', max_length=30)),
                ('uploaded_file', models.FileField(help_text="The CSV file as originally uploaded. Kept so a failed/cancelled job can be resumed or re-run.", upload_to='program_course_imports/%Y/%m/')),
                ('original_filename', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('RUNNING', 'Running'), ('COMPLETED', 'Completed'), ('COMPLETED_WITH_ERRORS', 'Completed with errors'), ('FAILED', 'Failed'), ('CANCELLED', 'Cancelled')], db_index=True, default='PENDING', max_length=25)),
                ('chunk_size', models.PositiveIntegerField(default=500)),
                ('total_rows', models.PositiveIntegerField(default=0)),
                ('processed_rows', models.PositiveIntegerField(default=0)),
                ('created_count', models.PositiveIntegerField(default=0)),
                ('updated_count', models.PositiveIntegerField(default=0)),
                ('skipped_count', models.PositiveIntegerField(default=0)),
                ('failed_count', models.PositiveIntegerField(default=0)),
                ('progress_percent', models.PositiveSmallIntegerField(default=0)),
                ('celery_task_id', models.CharField(blank=True, max_length=155, null=True)),
                ('error_log', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('initiated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='program_course_import_jobs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='importjob',
            index=models.Index(fields=['status', 'resource_type'], name='program_man_status_e5b1a1_idx'),
        ),
        migrations.AddIndex(
            model_name='importjob',
            index=models.Index(fields=['-created_at'], name='program_man_created_9e3c2f_idx'),
        ),
    ]
