from django.db import migrations


def normalize_existing_unit_types(apps, schema_editor):
    """
    One-off cleanup for any ProgramCourse rows that were saved before the
    typo-tolerant normalize_unit_type() logic existed (e.g. via CSV/Excel
    import, admin scripts, or old code paths that stored a raw string).
    Re-runs the same normalization the model now applies automatically on
    every save, so a mistyped value like 'Electve' or 'ELCTIVE' becomes a
    proper 'ELECTIVE' instead of silently behaving like a Core unit.
    """
    ProgramCourse = apps.get_model('program_management', 'ProgramCourse')

    # Historical models used in migrations don't carry custom methods, so
    # the normalization logic is duplicated here rather than imported.
    import difflib

    UNIT_TYPE_CHOICES = [
        ('CORE', 'Core'),
        ('ELECTIVE', 'Elective'),
        ('UNIVERSITY_WIDE', 'University Wide'),
        ('REQUIRED_ELECTIVE', 'Required Elective'),
    ]
    valid_codes = [c[0] for c in UNIT_TYPE_CHOICES]
    label_map = {label.strip().upper().replace(' ', '_'): code for code, label in UNIT_TYPE_CHOICES}

    def normalize(raw_value):
        if not raw_value:
            return 'CORE'
        cleaned = str(raw_value).strip().upper().replace(' ', '_').replace('-', '_')
        if cleaned in valid_codes:
            return cleaned
        if cleaned in label_map:
            return label_map[cleaned]
        candidates = valid_codes + list(label_map.keys())
        close = difflib.get_close_matches(cleaned, candidates, n=1, cutoff=0.6)
        if close:
            return label_map.get(close[0], close[0])
        return 'CORE'

    updates = []
    for pc in ProgramCourse.objects.all().only('id', 'unit_type'):
        fixed = normalize(pc.unit_type)
        if fixed != pc.unit_type:
            pc.unit_type = fixed
            updates.append(pc)

    if updates:
        ProgramCourse.objects.bulk_update(updates, ['unit_type'])


def noop_reverse(apps, schema_editor):
    # Normalization isn't reversible (original typo'd text isn't kept).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('program_management', '0003_alter_programcourse_options_and_more'),
    ]

    operations = [
        migrations.RunPython(normalize_existing_unit_types, noop_reverse),
    ]
