"""Management command to import RBC Annual Reports in batch."""

import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Account, FileImportLog, RbcAnnual
from imports.parsers_annual_vl import parse_rbc_annual_report_vl

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Import RBC Annual Reports from a directory or single file.'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', type=str,
                            help='Path to PDF file or directory.')
        parser.add_argument(
            '--inbox-dir',
            type=str,
            default=None,
            help='Path to inbox directory (overrides positional argument).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse PDFs but do not write to the database.',
        )

    def _find_annual_report_files(self, target_path: Path):
        """Find Annual Report PDF files in a directory or single file.
        
        Filters to only process files that start with 'annual report' (case-insensitive).
        This prevents the command from consuming statement PDFs.
        """
        if target_path.is_file():
            if target_path.suffix.lower() == '.pdf':
                return [target_path]
            else:
                raise CommandError(
                    f'File {target_path.name} is not a PDF file.'
                )
        elif target_path.is_dir():
            all_pdfs = sorted(target_path.glob('*.pdf'))
            # Filter to only annual report files (filename starts with "annual report")
            pdf_files = [f for f in all_pdfs if f.name.lower().startswith('annual report')]
            if not pdf_files:
                raise CommandError(
                    f'No Annual Report PDF files found in {target_path} '
                    f'(found {len(all_pdfs)} total PDFs, but none match "annual report" prefix)'
                )
            return pdf_files
        else:
            raise CommandError(f'Path does not exist: {target_path}')

    def _get_or_create_account(self, account_number: str, account_type_code: str,
                               account_name: str = ''):
        """Look up or create an Account matching the RBC account number."""
        import re
        canonical = re.sub(r'\D', '', account_number)[:8] if account_number else ''
        if canonical:
            try:
                return Account.objects.get(account_id=canonical)
            except Account.DoesNotExist:
                pass
            # Fallback: exact match on raw string
            try:
                return Account.objects.get(account_id=account_number)
            except Account.DoesNotExist:
                pass

        # Auto-create
        account_id = canonical or f'RBC-{account_type_code}'
        name = account_name or f'Auto-created from PDF: {account_number}'
        logger.warning("Auto-created account for unrecognised number %r", account_number)
        return Account.objects.create(
            account_id=account_id,
            name=name,
            broker='RBC',
        )

    def handle(self, *args, **options):
        inbox_dir = options['inbox_dir'] or options.get('path') or str(getattr(settings, 'IMPORT_INBOX_DIR', Path('inbox')))
        target_path = Path(inbox_dir)
        dry_run = options['dry_run']

        processed_dir = Path(getattr(settings, 'IMPORT_PROCESSED_DIR', Path('processed')))
        errors_dir = processed_dir / 'errors'
        processed_dir.mkdir(parents=True, exist_ok=True)
        errors_dir.mkdir(parents=True, exist_ok=True)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no database changes'))

        pdf_files = self._find_annual_report_files(target_path)

        files_processed = 0
        records_created = 0
        duplicates_skipped = 0
        errors = 0

        for pdf_file in pdf_files:
            files_processed += 1
            self.stdout.write(f'Processing: {pdf_file.name}')

            try:
                parsed = parse_rbc_annual_report_vl(str(pdf_file))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Failed to parse {pdf_file.name}: {e}'))
                errors += 1
                if not dry_run:
                    FileImportLog.objects.create(
                        filename=pdf_file.name,
                        broker='RBC',
                        imported_at=timezone.now(),
                        records_imported=0,
                        records_skipped=0,
                        status='error',
                        error_message=str(e),
                    )
                    dest = errors_dir / pdf_file.name
                    shutil.move(str(pdf_file), str(dest))
                    logger.info('Moved failed file to %s', dest)
                continue

            if dry_run:
                currencies = [c for c in ('cad', 'usd') if parsed.get(c) is not None]
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Would create RbcAnnual for {parsed.get("account_number")} '
                        f'year {parsed["report_year"]}: currencies={currencies}'
                    )
                )
                records_created += len(currencies)
                continue

            account = self._get_or_create_account(
                parsed['account_number'],
                parsed['account_type_code'],
                parsed.get('account_type_code', ''),
            )

            file_records_created = 0
            for currency, cdata in [('CAD', parsed.get('cad')), ('USD', parsed.get('usd'))]:
                if cdata is None:
                    continue

                existing = RbcAnnual.objects.filter(
                    account=account,
                    report_year=parsed['report_year'],
                    currency=currency,
                )
                if existing.exists():
                    self.stdout.write(
                        self.style.WARNING(
                            f'Skipping duplicate: {pdf_file.name} '
                            f'(account={account}, year={parsed["report_year"]}, currency={currency})'
                        )
                    )
                    duplicates_skipped += 1
                    continue

                RbcAnnual.objects.create(
                    account=account,
                    file_name=pdf_file.name,
                    report_year=parsed['report_year'],
                    report_period_start=parsed['report_period_start'],
                    report_period_end=parsed['report_period_end'],
                    account_number=parsed.get('account_number', ''),
                    account_type_code=parsed.get('account_type_code', ''),
                    currency=currency,
                    bal_beginning_value=cdata.get('bal_beginning_value', 0),
                    bal_deposits_in=cdata.get('bal_deposits_in', 0),
                    bal_withdrawals_out=cdata.get('bal_withdrawals_out', 0),
                    bal_net_return=cdata.get('bal_net_return', 0),
                    bal_ending_value=cdata.get('bal_ending_value', 0),
                    bal_amount_invested=cdata.get('bal_amount_invested', 0),
                    perf_since_date=cdata.get('perf_since_date'),
                    perf_1yr=cdata.get('perf_1yr'),
                    perf_3yr=cdata.get('perf_3yr'),
                    perf_5yr=cdata.get('perf_5yr'),
                    perf_10yr=cdata.get('perf_10yr'),
                    perf_since=cdata.get('perf_since'),
                    chg_operating=cdata.get('chg_operating', 0),
                    chg_commissions=cdata.get('chg_commissions', 0),
                    chg_total_transaction=cdata.get('chg_total_transaction', 0),
                    chg_total_paid=cdata.get('chg_total_paid', 0),
                    comp_trailing=cdata.get('comp_trailing', 0),
                    comp_other=cdata.get('comp_other', 0),
                    comp_total=cdata.get('comp_total', 0),
                )
                file_records_created += 1
                records_created += 1

            if file_records_created > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Imported: {pdf_file.name} -> {file_records_created} RbcAnnual record(s)'
                    )
                )
                FileImportLog.objects.create(
                    filename=pdf_file.name,
                    broker='RBC',
                    imported_at=timezone.now(),
                    records_imported=file_records_created,
                    records_skipped=0,
                    status='ok',
                    error_message='',
                )
                dest = processed_dir / pdf_file.name
                shutil.move(str(pdf_file), str(dest))
                logger.info('Moved processed file to %s', dest)
            else:
                # All records were duplicates — parsed OK, move to processed
                if not dry_run and pdf_file.exists():
                    dest = processed_dir / pdf_file.name
                    shutil.move(str(pdf_file), str(dest))
                    logger.info('Moved duplicate-only file to %s', dest)

        self.stdout.write(self.style.SUCCESS(
            f'\n=== Annual Report Import Summary ===\n'
            f'Files processed:   {files_processed}\n'
            f'Records created:   {records_created}\n'
            f'Duplicates skipped:{duplicates_skipped}\n'
            f'Errors:            {errors}\n'
        ))
