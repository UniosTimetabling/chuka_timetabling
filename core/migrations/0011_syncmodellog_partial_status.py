# Generated manually — choices-only change (no schema impact), adding
# "partial" so a chunk that the remote accepted (HTTP 200) but which had
# some individual rows rejected can be distinguished from a clean "sent"
# or a total "failed". See sync_views.receive_sync / sync_engine.run_sync.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_syncnode_auto_sync_interval_minutes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='syncmodellog',
            name='status',
            field=models.CharField(
                choices=[
                    ('sent', 'Sent'),
                    ('skipped', 'No changes — skipped'),
                    ('partial', 'Partially sent — some rows rejected'),
                    ('failed', 'Failed'),
                ],
                default='sent',
                max_length=10,
            ),
        ),
    ]
