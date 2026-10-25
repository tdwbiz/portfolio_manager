"""One-off free-text scrub: replace stored account numbers with account names.

Scans the free-text columns listed in ``TARGETS`` for real account numbers
(exact fingerprint matches only) and replaces them with the account name.
Dry run by default — nothing is written without ``--apply``. Only counts are
printed; no value from the database is ever shown.
"""

from django.core.management.base import BaseCommand

from core.models import (
    BrokerStatement,
    FileImportLog,
    RbcAnnual,
    RbcMonthly,
    RbcMonthlyTransaction,
    Transaction,
)
from core.utils.account_fingerprint import scrub_text

# (model, field) pairs — the scrub targets from docs/plans/number-columns.md.
TARGETS = [
    (Transaction, 'notes'),
    (RbcMonthlyTransaction, 'description'),
    (FileImportLog, 'filename'),
    (FileImportLog, 'error_message'),
    (BrokerStatement, 'raw_filename'),
    (RbcMonthly, 'file_name'),
    (RbcAnnual, 'file_name'),
]


class Command(BaseCommand):
    help = (
        'Replace stored account numbers in free-text fields with the account '
        'name. Dry run by default; use --apply to write.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Write the changes (default is a dry run that only reports counts).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        self.stdout.write(f'scrub_account_numbers [{"APPLY" if apply else "DRY-RUN"}]')

        for model, field in TARGETS:
            matched = replaced = unmatched = 0
            updates = []
            for obj in model.objects.all().iterator():
                value = getattr(obj, field) or ''
                if not value:
                    continue
                cleaned, m, r, u = scrub_text(value)
                matched += m
                replaced += r
                unmatched += u
                if cleaned != value:
                    updates.append((obj.pk, cleaned))

            if apply and updates:
                for pk, cleaned in updates:
                    model.objects.filter(pk=pk).update(**{field: cleaned})

            self.stdout.write(
                f'{model._meta.db_table},{field},'
                f'matched={matched},replaced={replaced},unmatched={unmatched}'
            )

        if apply:
            self.stdout.write(self.style.SUCCESS('done — changes written'))
        else:
            self.stdout.write(
                self.style.WARNING('dry run — no changes written; re-run with --apply')
            )
