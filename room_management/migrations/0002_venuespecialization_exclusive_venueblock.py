from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('room_management', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='venuespecialization',
            name='exclusive',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "If True, the listed venues are RESERVED ONLY for the designated "
                    "courses/programs/departments — no other course may ever be placed "
                    "there, even if the designated courses don't fill every timeslot. "
                    "If False (default), any free timeslots left in the venue after the "
                    "designated courses are scheduled may be given to other courses "
                    "(this is the original 'priority pass' behaviour)."
                ),
            ),
        ),
        migrations.CreateModel(
            name='VenueBlock',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(
                    blank=True, default='', max_length=255,
                    help_text="Why this venue is blocked, e.g. 'Under renovation', 'Reserved for Senate sittings'.",
                )),
                ('is_active', models.BooleanField(
                    default=True,
                    help_text='Uncheck to temporarily lift the block without deleting it.',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('venue', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='block_rule',
                    to='room_management.venue',
                    help_text='Venue to hide from the autoscheduler entirely.',
                )),
            ],
            options={
                'verbose_name': 'Venue Block',
                'verbose_name_plural': 'Venue Blocks',
                'ordering': ['venue__code'],
            },
        ),
    ]
