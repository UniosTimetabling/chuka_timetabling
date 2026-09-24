from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0009_studentgroup_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="studentgroup",
            name="letter",
            field=models.CharField(
                max_length=4,
                help_text="Group code, e.g. 'A', 'B', 'DA', 'DB'.",
            ),
        ),
    ]
