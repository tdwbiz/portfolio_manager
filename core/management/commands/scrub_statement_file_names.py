"""One-off: rewrite stored statement file names to the safe pattern (phase 2 step 11).

Rows created before the safe-name change mostly carry a last-4 or the full
account number in ``RbcMonthly.file_name`` / ``RbcAnnual.file_name``. This
command rewrites those values to ``<date>_<kind>_<ACCOUNT_NAME>_<hash8>.<ext>``
using each row's account name, statement date / report year and a hash8:

- when the physical file is found in ``processed/`` or ``processed/errors/``,
  the content hash of that file (``from-file``);
- when the source file is missing, the first 8 hex chars of
  HMAC-SHA256(``ACCOUNT_FINGERPRINT_KEY``, stored file name verbatim) —
  ``keyed_hash8`` — so rows whose PDFs are not on this machine still get a
  safe, deterministic name (``keyed``).

DB only — it never renames files on disk (tdw decision, Oct 25). Rows already
matching the safe pattern are left alone (a second run is a no-op). The
fingerprint key is validated before any row is touched: with a missing or
too-short ``ACCOUNT_FINGERPRINT_KEY`` the command raises ``CommandError`` in
both modes, so nothing is written — or reported as safe — without a key.
``--dry-run`` prints counts only — never names or numbers. tdw runs this on
mercury after a backup.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import RbcAnnual, RbcMonthly
from core.utils.account_fingerprint import keyed_hash8
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
            help='Print per-model counts (from-file / keyed / already-safe) without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # Key gate: a missing/short key means the keyed fallback cannot run,
        # so stop before touching any row — in both modes, so a dry run can
        # never report success for a real run that would then fail.
        try:
            keyed_hash8('')
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        processed_dir = Path(
            getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed')
        )
        search_dirs = (processed_dir, processed_dir / 'errors')

        for model, kind in ((RbcMonthly, MONTHLY), (RbcAnnual, ANNUAL)):
            from_file, keyed, already_safe = 0, 0, 0
            updates = []
            for row in model.objects.select_related('account').all():
                name = row.file_name or ''
                if is_safe_file_name(name):
                    already_safe += 1
                    continue
                path = self._find_file(search_dirs, name)
                date_part = (
                    row.statement_date if kind == MONTHLY else row.report_year
                )
                if path is not None:
                    hash8 = hash8_of(path)
                    from_file += 1
                else:
                    # Source PDF missing: keyed HMAC of the stored name
                    # verbatim (empty string when NULL).
                    hash8 = keyed_hash8(name)
                    keyed += 1
                new_name = safe_file_name(
                    name, row.account.name, kind, date=date_part, hash8=hash8
                )
                updates.append((row.pk, new_name))

            if not dry_run:
                for pk, new_name in updates:
                    model.objects.filter(pk=pk).update(file_name=new_name)

            prefix = '[dry-run] ' if dry_run else ''
            self.stdout.write(
                f'{prefix}{model.__name__}: from-file={from_file} '
                f'keyed={keyed} already-safe={already_safe}'
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
