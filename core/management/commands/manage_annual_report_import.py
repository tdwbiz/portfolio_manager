"""Management command to import RBC Annual Reports in batch."""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from decimal import Decimal

from core.models import Account, FileImportLog, RbcAnnualReport, RbcAnnualBalances
from core.models import RbcAnnualPerformance, RbcAnnualPerformanceReturn
from core.models import RbcAnnualCharges, RbcAnnualCompensation
from imports.parsers_annual import parse_rbc_annual_report

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
        """Find Annual Report PDF files in a directory or single file."""
        if target_path.is_file():
            if target_path.name.lower().startswith('annual report'):
                return [target_path]
            else:
                raise CommandError(
                    f'File {target_path.name} does not match Annual Report pattern.'
                )
        elif target_path.is_dir():
            pdf_files = sorted(target_path.glob('Annual Report*.pdf'))
            if not pdf_files:
                # Try all PDFs and filter
                all_pdfs = sorted(target_path.glob('*.pdf'))
                pdf_files = [f for f in all_pdfs
                             if f.name.lower().startswith('annual report')]
            if not pdf_files:
                raise CommandError(
                    f'No Annual Report PDF files found in {target_path}'
                )
            return pdf_files
        else:
            raise CommandError(f'Path does not exist: {target_path}')

    def _get_or_create_account(self, account_number: str, account_type_code: str,
                               account_name: str = ''):
        """Look up or create an Account matching the RBC account number."""
        if account_number:
            try:
                return Account.objects.get(account_id=account_number)
            except Account.DoesNotExist:
                pass

        # Try partial match on account number
        if account_number:
            partial = account_number.replace('-', '')
            for acct in Account.objects.filter(broker='RBC'):
                if partial in acct.account_id.replace('-', ''):
                    return acct

        # Auto-create
        name = account_name or f'RBC {account_type_code} {account_number}'
        return Account.objects.create(
            account_id=account_number or f'RBC-{account_type_code}',
            name=name,
            broker='RBC',
        )

    def handle(self, *args, **options):
        inbox_dir = options['inbox_dir'] or options.get('path') or str(getattr(settings, 'IMPORT_INBOX_DIR', Path('inbox')))
        target_path = Path(inbox_dir)
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no database changes'))

        pdf_files = self._find_annual_report_files(target_path)

        files_processed = 0
        reports_created = 0
        balances_created = 0
        performance_created = 0
        returns_created = 0
        charges_created = 0
        compensation_created = 0
        duplicates_skipped = 0
        errors = 0

        for pdf_file in pdf_files:
            files_processed += 1
            self.stdout.write(f'Processing: {pdf_file.name}')

            try:
                parsed = parse_rbc_annual_report(str(pdf_file))
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
                continue

            # Get or create account (skip DB writes in dry-run)
            if dry_run:
                account_name = f"DRY-RUN: {parsed.get('account_type_code', 'Unknown')} ({parsed.get('account_number', 'N/A')})"
                account = type('MockAccount', (), {'__str__': lambda self: account_name})()
            else:
                account = self._get_or_create_account(
                    parsed['account_number'],
                    parsed['account_type_code'],
                    parsed.get('account_type_code', ''),
                )

            # Check for duplicate (same account + report_year)
            if not dry_run:
                existing = RbcAnnualReport.objects.filter(
                    account=account,
                    report_year=parsed['report_year'],
                )
                if existing.exists():
                    self.stdout.write(
                        self.style.WARNING(
                            f'Skipping duplicate: {pdf_file.name} '
                            f'(account={account}, year={parsed["report_year"]})'
                        )
                    )
                    duplicates_skipped += 1
                    continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Would create RbcAnnualReport for {account} year {parsed["report_year"]}: '
                        f'{len(parsed["balances"])} balances, '
                        f'{len(parsed["performance"])} performance records, '
                        f'{len(parsed["charges"])} charges, '
                        f'{len(parsed["compensation"])} compensation records'
                    )
                )
                reports_created += 1
                balances_created += len(parsed['balances'])
                performance_created += len(parsed['performance'])
                returns_created += sum(len(p.get('returns', [])) for p in parsed['performance'])
                charges_created += len(parsed['charges'])
                compensation_created += len(parsed['compensation'])
                continue

            # Create RbcAnnualReport
            report = RbcAnnualReport.objects.create(
                account=account,
                broker='RBC',
                file_name=pdf_file.name,
                report_year=parsed['report_year'],
                report_period_start=parsed['report_period_start'],
                report_period_end=parsed['report_period_end'],
                account_number=parsed['account_number'],
                account_type_code=parsed['account_type_code'],
            )

            # Create balances
            for bal in parsed.get('balances', []):
                RbcAnnualBalances.objects.create(
                    rbc_annual_report=report,
                    currency=bal['currency'],
                    beginning_market_value=bal['beginning_market_value'],
                    deposits_transfers_in=bal['deposits_transfers_in'],
                    withdrawals_transfers_out=bal['withdrawals_transfers_out'],
                    net_investment_return=bal['net_investment_return'],
                    ending_market_value=bal['ending_market_value'],
                    amount_invested=bal['amount_invested'],
                )
                balances_created += 1

            # Create performance records
            for perf in parsed.get('performance', []):
                perf_record = RbcAnnualPerformance.objects.create(
                    rbc_annual_report=report,
                    currency=perf['currency'],
                    since_date=perf.get('since_date'),
                )
                performance_created += 1

                for ret in perf.get('returns', []):
                    RbcAnnualPerformanceReturn.objects.create(
                        performance=perf_record,
                        period_label=ret['period_label'],
                        return_pct=ret['return_pct'],
                    )
                    returns_created += 1

            # Create charges
            for charge in parsed.get('charges', []):
                RbcAnnualCharges.objects.create(
                    rbc_annual_report=report,
                    currency=charge['currency'],
                    charge_category=charge['category'],
                    amount=charge['amount'],
                    is_subtotal=charge.get('is_subtotal', False),
                )
                charges_created += 1

            # Create compensation
            for comp in parsed.get('compensation', []):
                RbcAnnualCompensation.objects.create(
                    rbc_annual_report=report,
                    currency=comp['currency'],
                    compensation_type=comp['type'],
                    amount=comp['amount'],
                )
                compensation_created += 1

            reports_created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'Imported: {pdf_file.name} -> {report} '
                    f'({balances_created} balances, {performance_created} performance, '
                    f'{returns_created} returns, {charges_created} charges, '
                    f'{compensation_created} compensation)'
                )
            )

        # Summary
        self.stdout.write(self.style.SUCCESS(
            f'\n=== Annual Report Import Summary ===\n'
            f'Files processed:      {files_processed}\n'
            f'Reports created:      {reports_created}\n'
            f'Balances created:     {balances_created}\n'
            f'Performance records:  {performance_created}\n'
            f'Return records:       {returns_created}\n'
            f'Charges created:      {charges_created}\n'
            f'Compensation records: {compensation_created}\n'
            f'Duplicates skipped:   {duplicates_skipped}\n'
            f'Errors:               {errors}\n'
        ))
