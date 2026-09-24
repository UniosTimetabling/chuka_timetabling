from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0007_specialintakegroup_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="specialintakegroup",
            name="semester",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "Semester 1"), (2, "Semester 2"), (3, "Semester 3")],
                help_text=(
                    "The cohort's own semester label (e.g. self-sponsored students may "
                    "sit in Semester 3 while others in the same programme/year are in "
                    "Semester 2). This does NOT restrict which curriculum semester's "
                    "courses can be pulled into the group — that is chosen explicitly "
                    "when pulling courses."
                ),
            ),
        ),
    ]
