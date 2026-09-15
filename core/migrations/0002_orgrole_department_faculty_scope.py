from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('department_management', '0001_initial'),
        ('faculty_management', '0001_initial'),
        ('core', '0001_initial'),
    ]

    operations = [
        # The old `unique=True` on `title` meant only one "COD", one "Dean",
        # one "Dean Admin" etc. could ever exist across the whole
        # university — creating a second one silently reassigned the role
        # instead of failing. `user` (OneToOne) is the real uniqueness
        # constraint, so we drop the bogus one here.
        migrations.AlterField(
            model_name='orgrole',
            name='title',
            field=models.CharField(max_length=120),
        ),
        migrations.AddField(
            model_name='orgrole',
            name='department',
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text='Department this role administers (COD / COD Admin).',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='org_roles',
                to='department_management.department',
            ),
        ),
        migrations.AddField(
            model_name='orgrole',
            name='faculty',
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text='Faculty this role administers (Dean / Dean Admin).',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='org_roles',
                to='faculty_management.faculty',
            ),
        ),
    ]
