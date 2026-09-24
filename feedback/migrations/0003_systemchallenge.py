from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('feedback', '0002_feedback_mobile_and_attachments'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemChallenge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference_code', models.CharField(blank=True, help_text='Auto-generated tracking code, e.g. SC-2026-0001. Also used to match rows on re-import so re-importing an export updates the same record instead of duplicating it.', max_length=30, unique=True)),
                ('title', models.CharField(max_length=255)),
                ('classification', models.CharField(choices=[('system', 'System Challenge'), ('not_system', 'Not a System Challenge')], default='system', max_length=20, verbose_name='Is this a system challenge?')),
                ('description', models.TextField(help_text='What happened / what the challenge was.')),
                ('cause', models.TextField(verbose_name='What caused it')),
                ('resolution', models.TextField(blank=True, verbose_name='How it was resolved')),
                ('prevention', models.TextField(blank=True, verbose_name='How future recurrence is prevented')),
                ('involved_parties', models.TextField(blank=True, help_text='Names, roles or departments of everyone involved (reporter, affected users, staff who investigated/fixed it, etc.).', verbose_name='Involved users / parties')),
                ('status', models.CharField(choices=[('open', 'Open'), ('in_progress', 'In Progress'), ('resolved', 'Resolved'), ('monitoring', 'Monitoring')], default='open', max_length=20)),
                ('occurred_at', models.DateField(default=django.utils.timezone.localdate)),
                ('resolved_at', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='system_challenges_logged', to=settings.AUTH_USER_MODEL)),
                ('feedback', models.ForeignKey(blank=True, help_text='The feedback item this challenge was logged from, if any.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='system_challenges', to='feedback.feedback')),
            ],
            options={
                'verbose_name': 'System Challenge',
                'verbose_name_plural': 'System Challenge Log',
                'ordering': ['-occurred_at', '-created_at'],
            },
        ),
    ]
