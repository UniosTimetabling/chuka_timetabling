from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('special_requests', '0004_specialrequest_venue_constraint_choices'),
    ]

    operations = [
        migrations.AlterField(
            model_name='specialrequest',
            name='panel',
            field=models.CharField(
                choices=[
                    ('normal', 'COD (Regular)'),
                    ('campuses', 'Campus Allocation'),
                    ('resit', 'Resit Allocation'),
                    ('odel', 'ODEL Allocation'),
                    ('lab', 'Lab Allocation'),
                ],
                default='normal',
                help_text="Which allocation panel this SR was raised from — regular /cod/, campus, resit, ODEL, or lab.",
                max_length=20,
            ),
        ),
    ]
