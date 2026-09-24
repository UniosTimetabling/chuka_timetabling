from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_alter_syncmodellog_options_syncmodellog_pass_number_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='mobile_app_share_url',
            field=models.URLField(blank=True, default='', help_text='Optional custom link people are sent to when they share/install the app (Play Store, a Drive/OneDrive link, your own download page, etc.). When set it overrides the uploaded APK and the default store link — on the Mobile Analytics QR code AND inside the mobile app\'s own "Invite someone" screen. Leave blank to use the default.', max_length=500),
        ),
    ]
