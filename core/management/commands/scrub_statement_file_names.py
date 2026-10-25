"""One-off: rewrite stored statement file names to the safe pattern (phase 2 step 11).

Rows created before the safe-name change mostly carry a last-4 or the full
account number in ``RbcMonthly.file_name`` / ``RbcAnnual.file_name``. This
command rewrites those values to ``<date>_<kind>_<ACCOUNT_NAME>_<hash8>.<ext>``
using each row's account name, statement date / report year and the content
hash8 of the physical file located by its stored name in ``processed/`` or
``processed/errors/``.

DB only — it never renames files on disk (tdw decision, Oct 25). Rows whose
source file is missing are skipped and counted; rows already matching the
safe pattern are left alone (a second run is a no-op). ``--dry-run`` prints
counts only — never names or numbers. tdw runs this on mercury after a backup.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import RbcAnnual, RbcMonthly
from core.utils.safe_names import ANNUAL, MONTHLY, hash8_of, is_safe_file_name, safe_file_name


class Command(BaseCommand):
    help = (
        'Rewrite stored RbcMonthly.file_name / RbcAnnual.file_name values to '
        'the safe pattern (DB only; --dry-run prints counts only).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print per-model counts (to-rename / already-safe / missing-source) without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        processed_dir = Path(
            getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed')
        )
        search_dirs = (processed_dir, processed_dir / 'errors')

        for model, kind in ((RbcMonthly, MONTHLY), (RbcAnnual, ANNUAL)):
            to_rename, already_safe, missing_source = 0, 0, 0
            updates = []
            for row in model.objects.select_related('account').all():
                name = row.file_name or ''
                if is_safe_file_name(name):
                    already_safe += 1
                    continue
                path = self._find_file(search_dirs, name)
                if path is None:
                    missing_source += 1
                    continue
                date_part = (
                    row.statement_date if kind == MONTHLY else row.report_year
                )
                new_name = safe_file_name(
                    name, row.account.name, kind, date=date_part, hash8=hash8_of(path)
                )
                updates.append((row.pk, new_name))
                to_rename += 1

            if not dry_run:
                for pk, new_name in updates:
                    model.objects.filter(pk=pk).update(file_name=new_name)

            prefix = '[dry-run] ' if dry_run else ''
            self.stdout.write(
                f'{prefix}{model.__name__}: to-rename={to_rename} '
                f'already-safe={already_safe} missing-source={missing_source}'
            )

    @staticmethod
    def _find_file(search_dirs, name: str) -> Path | None:
        """Locate the physical file by its stored (plain) name."""
        base = Path(name).name  # never trust a stored value to be path-safe
        for directory in search_dirs:
            candidate = directory / base
            if candidate.is_file():
                return candidate
        return None
