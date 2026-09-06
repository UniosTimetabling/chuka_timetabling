import secrets

from django.db import migrations, models
import django.db.models.deletion


def backfill_watermark_secrets(apps, schema_editor):
    """Existing template rows won't get a secret until the model's save()
    runs again (e.g. next admin edit). Set one immediately so watermarking
    works on the very next PDF export, not just after someone opens admin."""
    TimetablePdfTemplate = apps.get_model('export_import', 'TimetablePdfTemplate')
    for template in TimetablePdfTemplate.objects.filter(watermark_secret=''):
        template.watermark_secret = secrets.token_hex(24)
        template.save(update_fields=['watermark_secret'])


class Migration(migrations.Migration):

    dependencies = [
        ('export_import', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='timetablepdftemplate',
            name='director_full_name',
            field=models.CharField(
                default='Prof. Grace Abucheli, Ph.D',
                help_text="Director's full name with title/qualifications, as it should appear "
                          "on the signature line, e.g. 'Prof. Grace Abucheli, Ph.D'",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='timetablepdftemplate',
            name='director_initials',
            field=models.CharField(
                default='GAO',
                help_text="Director's initials shown UPPERCASE at the start of the reference "
                          "code, e.g. 'GAO' in 'GAO/fm/sk'",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='timetablepdftemplate',
            name='watermark_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Show the directorate watermark on generated PDFs',
            ),
        ),
        migrations.AddField(
            model_name='timetablepdftemplate',
            name='watermark_secret',
            field=models.CharField(
                blank=True,
                editable=False,
                help_text='Auto-generated secret key used to derive a unique, non-forgeable '
                          'watermark code per document. Never exposed in full; not manually '
                          'editable.',
                max_length=64,
            ),
        ),
        migrations.CreateModel(
            name='PdfSignatory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('full_name', models.CharField(help_text="Full name, for reference only, e.g. 'Fred Mwangi'", max_length=200)),
                ('initials_code', models.CharField(help_text="Lowercase initials as they should appear in the signature code, e.g. 'fm'", max_length=6)),
                ('order', models.PositiveIntegerField(default=0, help_text="Controls left-to-right position after the director's initials")),
                ('is_active', models.BooleanField(default=True, help_text='Untick to exclude this person from the signature code without deleting them')),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='signatories', to='export_import.timetablepdftemplate')),
            ],
            options={
                'verbose_name': 'PDF Signatory',
                'verbose_name_plural': 'PDF Signatories',
                'ordering': ['order', 'id'],
            },
        ),
        migrations.RunPython(backfill_watermark_secrets, migrations.RunPython.noop),
    ]
