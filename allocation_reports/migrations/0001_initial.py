from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('department_management', '0001_initial'),
        ('campuses_timetable', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AllocationPdfRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.CharField(choices=[
                    ('main', 'Main Allocation'),
                    ('odel', 'ODeL Allocation'),
                    ('campus', 'Campus Allocation'),
                    ('resit', 'Resit Allocation'),
                ], db_index=True, max_length=10)),
                ('version', models.PositiveIntegerField(default=1)),
                ('is_current', models.BooleanField(db_index=True, default=True)),
                ('file', models.FileField(blank=True, max_length=400, null=True, upload_to='allocation_pdfs')),
                ('row_count', models.PositiveIntegerField(default=0)),
                ('signature', models.CharField(blank=True, default='', max_length=64)),
                ('status', models.CharField(choices=[
                    ('pending', 'Queued'),
                    ('generating', 'Generating'),
                    ('ready', 'Ready'),
                    ('failed', 'Failed'),
                    ('no_data', 'No data yet'),
                ], db_index=True, default='pending', max_length=12)),
                ('error_message', models.TextField(blank=True, default='')),
                ('generated_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('campus', models.ForeignKey(blank=True, help_text="Only set when scope='campus'.", null=True,
                                              on_delete=django.db.models.deletion.CASCADE,
                                              related_name='allocation_pdf_runs', to='campuses_timetable.campus')),
                ('department', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                  related_name='allocation_pdf_runs',
                                                  to='department_management.department')),
                ('generated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                     related_name='generated_allocation_pdfs',
                                                     to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-version'],
            },
        ),
        migrations.AddIndex(
            model_name='allocationpdfrun',
            index=models.Index(fields=['scope', 'department', 'campus', 'is_current'], name='alloc_pdf_scope_dept_idx'),
        ),
        migrations.AddConstraint(
            model_name='allocationpdfrun',
            constraint=models.UniqueConstraint(fields=('scope', 'department', 'campus', 'version'),
                                                name='uniq_allocation_pdf_version'),
        ),
    ]
