"""Management command to import RBC PDF statements in batch."""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from decimal import Decimal

from core.models import Account, PdfStatement, PdfAccountBalance, PdfTransaction
from imports.parsers_pdf import parse_rbc_pdf


class Command(BaseCommand):
    help = 'Import RBC PDF statements from a directory or single file.'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', type=str, help='Path to PDF file or directory.')
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

    def handle(self, *args, **options):
        # Use inbox_dir if provided, otherwise fall back to positional path
        inbox_dir = options['inbox_dir']
        target_path = Path(inbox_dir) if inbox_dir else Path(options['path'])
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no database changes'))

        if target_path.is_file():
            pdf_files = [target_path]
        elif target_path.is_dir():
            pdf_files = sorted(target_path.glob('*.pdf'))
            if not pdf_files:
                raise CommandError(f'No PDF files found in {target_path}')
        else:
            raise CommandError(f'Path does not exist: {target_path}')

        files_processed = 0
        statements_created = 0
        transactions_imported = 0
        balances_recorded = 0
        duplicates_skipped = 0
        errors = 0

        for pdf_file in pdf_files:
            files_processed += 1
            self.stdout.write(f'Processing: {pdf_file.name}')

            try:
                parsed = parse_rbc_pdf(str(pdf_file))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Failed to parse {pdf_file.name}: {e}'))
                errors += 1
                continue

            statement_date = parsed.get('statement_date')
            account_number = parsed.get('account_number', '')

            if not statement_date:
                self.stderr.write(self.style.ERROR(
                    f'Skipping {pdf_file.name}: could not determine statement date'
                ))
                errors += 1
                continue

            # Look up or create Account
            account = None
            if account_number:
                try:
                    account = Account.objects.get(account_id=account_number)
                except Account.DoesNotExist:
                    # Auto-create account if not found
                    account = Account.objects.create(
                        account_id=account_number,
                        name=f'Auto-created from PDF: {account_number}',
                        broker='RBC',
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Auto-created account: {account_number}'
                        )
                    )

            if account is None:
                self.stderr.write(self.style.ERROR(
                    f'No account number found in PDF. Skipping {pdf_file.name}.'
                ))
                continue

            # Check for duplicate
            existing = PdfStatement.objects.filter(
                account=account,
                statement_date=statement_date,
            )
            if existing.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f'Skipping duplicate: {pdf_file.name} '
                        f'(account={account}, date={statement_date})'
                    )
                )
                duplicates_skipped += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Would create PdfStatement for {account} on {statement_date} '
                        f'with {len(parsed["transactions"])} transactions '
                        f'and {len(parsed["balances"])} balances'
                    )
                )
                statements_created += 1
                transactions_imported += len(parsed['transactions'])
                balances_recorded += len(parsed['balances'])
                continue

            # Create PdfStatement
            pdf_statement = PdfStatement.objects.create(
                account=account,
                file_name=pdf_file.name,
                statement_date=statement_date,
                last_statement_date=parsed.get('last_statement_date'),
                account_number=account_number,
                fx_rate=parsed.get('fx_rate', Decimal('1.000000')),
            )

            # Create PdfAccountBalance records
            for bal in parsed.get('balances', []):
                PdfAccountBalance.objects.create(
                    pdf_statement=pdf_statement,
                    account_type=bal['account_type'],
                    opening_balance=bal.get('opening', Decimal('0.00')),
                    closing_balance=bal.get('closing', Decimal('0.00')),
                    market_value=bal.get('market_value', Decimal('0.00')),
                    percentage=bal.get('percentage', Decimal('0.00')),
                )
                balances_recorded += 1

            # Create PdfTransaction records
            for tx in parsed.get('transactions', []):
                PdfTransaction.objects.create(
                    pdf_statement=pdf_statement,
                    date=tx['date'],
                    activity_type=tx['activity_type'],
                    description=tx['description'],
                    symbol=tx.get('symbol', ''),
                    quantity=tx.get('quantity', Decimal('0.00')),
                    price=tx.get('price', Decimal('0.00')),
                    debit=tx.get('debit', Decimal('0.00')),
                    credit=tx.get('credit', Decimal('0.00')),
                    currency=tx.get('currency', 'CAD'),
                    commission=tx.get('commission', Decimal('0.00')),
                )
                transactions_imported += 1

            statements_created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'Imported: {pdf_file.name} -> {statements_created} statements, '
                    f'{transactions_imported} transactions, {balances_recorded} balances'
                )
            )

        # Summary
        self.stdout.write(self.style.SUCCESS(
            f'\n=== Import Summary ===\n'
            f'Files processed:    {files_processed}\n'
            f'Statements created: {statements_created}\n'
            f'Transactions imported: {transactions_imported}\n'
            f'Balances recorded:  {balances_recorded}\n'
            f'Duplicates skipped: {duplicates_skipped}\n'
            f'Errors:             {errors}\n'
        ))
