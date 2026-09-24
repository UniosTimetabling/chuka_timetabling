from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import mobile_api.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("mobile_api", "0006_deviceinstall"),
    ]

    operations = [
        migrations.CreateModel(
            name="SharedFile",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(max_length=500, upload_to=mobile_api.models.shared_file_upload_path)),
                ("title", models.CharField(blank=True, default="", max_length=200,
                                            help_text="Optional display name. Defaults to the filename if left blank.")),
                ("mime_type", models.CharField(blank=True, default="", max_length=100)),
                ("file_size", models.PositiveIntegerField(default=0, help_text="Bytes, captured at upload time.")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("uploaded_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="mobile_shared_files",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Shared file",
                "verbose_name_plural": "Shared files",
                "ordering": ["-uploaded_at"],
            },
        ),
    ]
