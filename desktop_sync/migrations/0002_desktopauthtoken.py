from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import desktop_sync.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("desktop_sync", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DesktopAuthToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(default=desktop_sync.models._generate_key, editable=False, max_length=48, unique=True)),
                ("device_label", models.CharField(blank=True, default="", max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="desktop_tokens", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="desktopauthtoken",
            index=models.Index(fields=["key"], name="desktop_syn_key_idx"),
        ),
    ]
