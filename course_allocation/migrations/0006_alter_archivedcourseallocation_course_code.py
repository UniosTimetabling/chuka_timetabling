from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0005_specializationcategory_specializationstem_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="archivedcourseallocation",
            name="course_code",
            # Was max_length=20, which is shorter than ProgramCourse.course_code
            # (max_length=100), the field this value is actually copied from
            # during archiving. Any course code over 20 chars caused a MySQL
            # "Data too long for column 'course_code'" DataError, which
            # crashed /auto-allocate/run/ with a 500 mid-transaction.
            field=models.CharField(max_length=100),
        ),
    ]
