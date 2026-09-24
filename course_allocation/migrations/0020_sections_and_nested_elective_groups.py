# Hand-written: numbered course sections + elective (pick-one) groups nested
# inside specialization stems.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_allocation', '0019_rename_course_allo_departm_a1b2c3_idx_course_allo_departm_2b3f10_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseallocation',
            name='section_number',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                help_text=(
                    "Set when this course has been split into numbered SECTIONS "
                    "(e.g. COSC 103 has too many students for one class): 1 for the "
                    "original row, 2, 3, ... for the extra classes. Every section "
                    "keeps the same base course code and the same student group / "
                    "stem / elective-group mapping. Sections partition the cohort's "
                    "students, so they may be scheduled at the SAME time without "
                    "being a collision. NULL = the course has not been split."
                ),
            ),
        ),
        migrations.AddField(
            model_name='selectiongroup',
            name='specialization_stems',
            field=models.ManyToManyField(
                blank=True, related_name='elective_groups',
                to='course_allocation.specializationstem',
                help_text=(
                    "Optional: the stem(s) this pick-ONE pool is nested inside "
                    "(e.g. a stem of 12 units where 10 are core and the student "
                    "chooses one of the last two). The pool's courses are also "
                    "members of the stem, so they still clash with the stem's core "
                    "units, but not with each other. Leave empty for an ordinary "
                    "program-level elective pool."
                ),
            ),
        ),
    ]
