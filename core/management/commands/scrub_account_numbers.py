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
from core.utils.account_fingerprint import build_fingerprint_name_map, scrub_text

# Pks fetched per chunk. Must stay small enough that bulk_update (about 2
# parameters per row per field plus the pks) stays under SQL Server's limit
# of 2100 parameters per statement.
CHUNK_SIZE = 200

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
        # One query for the fingerprint->name map; scrub_text never queries
        # per candidate (SQL Server without MARS).
        name_map = build_fingerprint_name_map()
        self.stdout.write(f'scrub_account_numbers [{"APPLY" if apply else "DRY-RUN"}]')

        for model, field in TARGETS:
            matched = replaced = unmatched = 0
            # Materialize the pks first so no result set is open while we
            # fetch and write (SQL Server without MARS).
            pks = list(model.objects.values_list('pk', flat=True))
            for i in range(0, len(pks), CHUNK_SIZE):
                chunk = pks[i:i + CHUNK_SIZE]
                objs = list(model.objects.filter(pk__in=chunk))
                changed = []
                for obj in objs:
                    value = getattr(obj, field) or ''
                    if not value:
                        continue
                    cleaned, m, r, u = scrub_text(value, name_map)
                    matched += m
                    replaced += r
                    unmatched += u
                    if cleaned != value:
                        setattr(obj, field, cleaned)
                        changed.append(obj)
                if apply and changed:
                    model.objects.bulk_update(changed, [field], batch_size=CHUNK_SIZE)

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
