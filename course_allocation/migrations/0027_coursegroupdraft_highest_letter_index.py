from django.db import migrations, models


def _backfill_highest_letter_index(apps, schema_editor):
    """Existing drafts predate this ratchet field — for every one of them,
    every letter from 1..num_groups already exists with no deletions yet
    (delete_letter didn't exist before this migration), so num_groups IS
    the correct starting highest_letter_index."""
    CourseGroupDraft = apps.get_model("course_allocation", "CourseGroupDraft")
    CourseGroupDraft.objects.update(highest_letter_index=models.F("num_groups"))


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("course_allocation", "0026_coursegroupdraftletter_lecturer"),
    ]

    operations = [
        migrations.AddField(
            model_name="coursegroupdraft",
            name="highest_letter_index",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(_backfill_highest_letter_index, _noop),
    ]
