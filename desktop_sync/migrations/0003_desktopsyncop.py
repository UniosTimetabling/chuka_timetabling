from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("desktop_sync", "0002_desktopauthtoken"),
    ]

    operations = [
        migrations.CreateModel(
            name="DesktopSyncOp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("local_op_id", models.CharField(max_length=64, unique=True)),
                ("kind", models.CharField(max_length=16)),
                ("op", models.CharField(max_length=16)),
                ("entry_pk", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="desktopsyncop",
            index=models.Index(fields=["created_at"], name="desktop_syn_op_created_idx"),
        ),
    ]
