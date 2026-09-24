from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="SyncMeta",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_id", models.PositiveIntegerField()),
                ("version", models.PositiveIntegerField(default=1)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_by", models.CharField(blank=True, default="", max_length=150)),
                ("deleted", models.BooleanField(default=False)),
                ("content_type", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="contenttypes.contenttype")),
            ],
        ),
        migrations.AddIndex(
            model_name="syncmeta",
            index=models.Index(fields=["content_type", "version"], name="desktop_syn_content_ver_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="syncmeta",
            unique_together={("content_type", "object_id")},
        ),
    ]
