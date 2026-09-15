from django.db import migrations, models
import django.db.models.deletion
import feedback.models


class Migration(migrations.Migration):

    dependencies = [
        ('feedback', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='feedback',
            name='source',
            field=models.CharField(choices=[('web', 'Web'), ('mobile', 'Mobile App')], default='web', max_length=10),
        ),
        migrations.AddField(
            model_name='feedback',
            name='role',
            field=models.CharField(blank=True, choices=[('student', 'Student'), ('lecturer', 'Lecturer')], default='', max_length=10),
        ),
        migrations.AddField(
            model_name='feedback',
            name='mobile_user_id',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.CreateModel(
            name='FeedbackAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to=feedback.models.feedback_attachment_path)),
                ('name', models.CharField(blank=True, max_length=255)),
                ('mime_type', models.CharField(blank=True, max_length=100)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('feedback', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='feedback.feedback')),
            ],
        ),
    ]
