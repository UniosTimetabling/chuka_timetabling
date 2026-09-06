from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0005_remove_lecturertimepreference_lecturer_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='SyncNode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('host', 'Host — sends data to a remote'), ('remote', 'Remote — receives data from a host')], default='host', max_length=10)),
                ('is_enabled', models.BooleanField(default=False)),
                ('remote_url', models.URLField(blank=True, default='')),
                ('shared_token_encrypted', models.TextField(blank=True, default='')),
                ('sync_batch_delay_seconds', models.FloatField(default=1.5)),
                ('last_sync_started_at', models.DateTimeField(blank=True, null=True)),
                ('last_sync_finished_at', models.DateTimeField(blank=True, null=True)),
                ('last_sync_status', models.CharField(blank=True, default='', max_length=20)),
            ],
            options={
                'verbose_name': 'Sync Node Settings',
                'verbose_name_plural': 'Sync Node Settings',
            },
        ),
        migrations.CreateModel(
            name='SyncRun',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('running', 'Running'), ('success', 'Completed'), ('partial', 'Completed with errors'), ('failed', 'Failed')], default='running', max_length=10)),
                ('remote_url', models.URLField(blank=True, default='')),
                ('peer_run_id', models.CharField(blank=True, default='', max_length=64)),
                ('total_models', models.PositiveIntegerField(default=0)),
                ('models_completed', models.PositiveIntegerField(default=0)),
                ('records_sent', models.PositiveIntegerField(default=0)),
                ('error_message', models.TextField(blank=True, default='')),
                ('triggered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Sync Run',
                'verbose_name_plural': 'Sync Runs',
                'ordering': ['-started_at'],
            },
        ),
        migrations.CreateModel(
            name='SyncModelLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.PositiveIntegerField()),
                ('app_label', models.CharField(max_length=100)),
                ('model_name', models.CharField(max_length=100)),
                ('records_changed', models.PositiveIntegerField(default=0)),
                ('records_total', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('sent', 'Sent'), ('skipped', 'No changes — skipped'), ('failed', 'Failed')], default='sent', max_length=10)),
                ('error_message', models.TextField(blank=True, default='')),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='model_logs', to='core.syncrun')),
            ],
            options={
                'verbose_name': 'Sync Model Log',
                'verbose_name_plural': 'Sync Model Logs',
                'ordering': ['run', 'order'],
            },
        ),
        migrations.CreateModel(
            name='SyncRecordState',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('app_label', models.CharField(max_length=100)),
                ('model_name', models.CharField(max_length=100)),
                ('object_id', models.CharField(max_length=64)),
                ('content_hash', models.CharField(max_length=64)),
                ('last_synced_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Sync Record State',
                'verbose_name_plural': 'Sync Record States',
            },
        ),
        migrations.AddIndex(
            model_name='syncrecordstate',
            index=models.Index(fields=['app_label', 'model_name'], name='core_syncre_app_lab_9b6f7f_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='syncrecordstate',
            unique_together={('app_label', 'model_name', 'object_id')},
        ),
    ]
