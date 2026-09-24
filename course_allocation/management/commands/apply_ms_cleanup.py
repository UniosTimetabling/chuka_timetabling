# course_allocation/management/commands/apply_ms_cleanup.py
"""
Applies ms_cleanup_report.csv (produced by clean_management_science.py)
directly to the CourseAllocation table, instead of re-importing the whole
course_allocations.csv through the admin's Import button.

Re-importing the full CSV is NOT safe here: CourseAllocationResource has
import_id_fields = [] (see course_allocation/admin.py), so django-import-
export treats every row as a brand new record - it would duplicate every
department's allocations, not just Management Science's, and it also
won't delete the rows we determined are duplicates.

This command instead matches each report row to its existing DB row by
(department="Management Science", program, course_code) - the same triple
that was unique in the source CSV - and:
  - action == DROP_DUPLICATE  -> deletes the matching row
  - action == UPDATE_COUNT    -> sets number_of_students to new_students
  - anything else (OK, REVIEW_*) -> left untouched

Usage:
    python manage.py apply_ms_cleanup ms_cleanup_report.csv            # dry run
    python manage.py apply_ms_cleanup ms_cleanup_report.csv --apply    # do it
"""
import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from course_allocation.models import CourseAllocation
from department_management.models import Department
from program_management.models import Program

BASE_ACTIONS = {'DROP_DUPLICATE', 'UPDATE_COUNT'}
# Opt-in only (--include-review-drops): the Word doc is being treated as the
# department's source of truth for THIS semester, so anything it doesn't
# mention, or only mentions under a non-main-campus section, gets dropped
# too. REVIEW_AMBIGUOUS_IN_DOCUMENT and REVIEW_UNPARSEABLE_CODE are NOT
# included here - those still need a human to look at them.
REVIEW_DROP_ACTIONS = {'REVIEW_NOT_IN_DOCUMENT', 'REVIEW_ONLY_NON_MAIN_CAMPUS'}
DEPARTMENT_NAME = 'Management Science'


class Command(BaseCommand):
    help = 'Apply ms_cleanup_report.csv (drops + count fixes) to CourseAllocation'

    def add_arguments(self, parser):
        parser.add_argument('report_csv', help='path to ms_cleanup_report.csv')
        parser.add_argument('--apply', action='store_true',
                             help='write changes (default is dry-run)')
        parser.add_argument('--include-review-drops', action='store_true',
                             help='also delete REVIEW_NOT_IN_DOCUMENT and '
                                  'REVIEW_ONLY_NON_MAIN_CAMPUS rows (treats the '
                                  'Word doc as the department\'s full source of '
                                  'truth for this semester). Leaves '
                                  'REVIEW_AMBIGUOUS_IN_DOCUMENT and '
                                  'REVIEW_UNPARSEABLE_CODE untouched either way.')

    def handle(self, *args, **options):
        report_path = options['report_csv']
        apply_changes = options['apply']
        applied_actions = set(BASE_ACTIONS)
        if options['include_review_drops']:
            applied_actions |= REVIEW_DROP_ACTIONS

        try:
            department = Department.objects.get(name=DEPARTMENT_NAME)
        except Department.DoesNotExist:
            raise CommandError(f'Department "{DEPARTMENT_NAME}" not found')

        with open(report_path, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))

        to_process = [r for r in rows if r['action'] in applied_actions]
        self.stdout.write(f'{len(to_process)} of {len(rows)} report rows are '
                           f'in {sorted(applied_actions)} (rest are left untouched)')

        program_cache = {}

        def get_program(name):
            if name not in program_cache:
                program_cache[name] = Program.objects.filter(name=name).first()
            return program_cache[name]

        deleted, updated, not_found, ambiguous = 0, 0, [], []
        deleted_by_action = {}

        with transaction.atomic():
            for r in to_process:
                program = get_program(r['program'])
                qs = CourseAllocation.objects.filter(
                    department=department,
                    course_code=r['course_code'],
                )
                if program is not None:
                    qs = qs.filter(program=program)

                matches = list(qs)
                if len(matches) == 0:
                    not_found.append(r)
                    continue
                if len(matches) > 1:
                    ambiguous.append(r)
                    continue

                obj = matches[0]
                if r['action'] in ('DROP_DUPLICATE',) | REVIEW_DROP_ACTIONS:
                    self.stdout.write(f'  DELETE[{r["action"]}]  {r["course_code"]!r} '
                                       f'({r["program"]}) - {r["note"]}')
                    if apply_changes:
                        obj.delete()
                    deleted += 1
                    deleted_by_action[r['action']] = deleted_by_action.get(r['action'], 0) + 1
                elif r['action'] == 'UPDATE_COUNT':
                    self.stdout.write(f'  UPDATE  {r["course_code"]!r} '
                                       f'({r["program"]}) students '
                                       f'{r["old_students"]} -> {r["new_students"]}')
                    if apply_changes:
                        obj.number_of_students = int(r['new_students'])
                        obj.save(update_fields=['number_of_students'])
                    updated += 1

            if not apply_changes:
                # make extra sure nothing sticks even if a signal/save hook
                # did something unexpected during the dry run
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f'\n{"Applied" if apply_changes else "Would apply"}: '
            f'{deleted} deletes ({deleted_by_action}), {updated} updates'))
        if not_found:
            self.stdout.write(self.style.WARNING(
                f'{len(not_found)} report rows had no matching DB row '
                f'(already fixed manually? re-run against a fresh export)'))
            for r in not_found[:10]:
                self.stdout.write(f'    {r["action"]}  {r["course_code"]!r}  ({r["program"]})')
        if ambiguous:
            self.stdout.write(self.style.WARNING(
                f'{len(ambiguous)} report rows matched more than one DB row - '
                f'skipped, needs manual look'))
            for r in ambiguous[:10]:
                self.stdout.write(f'    {r["action"]}  {r["course_code"]!r}  ({r["program"]})')

        if not apply_changes:
            self.stdout.write('\nDry run only - re-run with --apply to write changes.')