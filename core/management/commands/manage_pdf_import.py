"""Management command to import RBC PDF statements in batch."""

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from decimal import Decimal

from core.models import Account, FileImportLog, RbcMonthly, RbcMonthlyBalance, RbcMonthlyTransaction
from imports.parsers_monthly_vl import parse_rbc_monthly_vl, VLParseError

import re

logger = logging.getLogger(__name__)

# ANSI escape code pattern
_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*m')


class TerminalLogger:
    """Dual-output logger that writes to both terminal and file."""
    def __init__(self, log_path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = open(self.log_path, 'a', encoding='utf-8', newline='\n')
        self.terminal = sys.stdout

    def write(self, message):
        # Write to terminal with ANSI codes (for colored output)
        self.terminal.write(message)
        # Write to file WITHOUT ANSI codes (for readability in Notepad)
        clean = _ANSI_ESCAPE.sub('', message)
        # Ensure proper newline at end
        if clean and not clean.endswith('\n'):
            clean += '\n'
        self.log_file.write(clean)
        self.log_file.flush()

    def flush(self):
        self.terminal.flush()
        self.log_file.flush()

    def close(self):
        self.log_file.close()

    def style(self, css_class):
        """Mimic Django's style() method to return the string with ANSI codes."""
        # Map Django CSS classes to ANSI escape codes
        style_map = {
            'ERROR': '\x1b[31;1m',      # Red bold
            'SUCCESS': '\x1b[32;1m',    # Green bold
            'WARNING': '\x1b[33;1m',    # Yellow bold
            'MIGRATE_LABEL': '\x1b[36;1m',  # Cyan bold
        }
        reset = '\x1b[0m'
        code = style_map.get(css_class, '')

        class StyledString:
            def __init__(self, text, code, reset):
                self.text = text
                self.code = code
                self.reset = reset

            def __str__(self):
                return f'{self.code}{self.text}{self.reset}'

            def write(self, message):
                self.terminal.write(str(self))

        return lambda text: StyledString(str(text), code, reset)


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

    def _move_file(self, src_path, dest_path, dest_folder, error_count=0):
        """Move a file with retry logic for Windows file locks."""
        try:
            shutil.copy2(str(src_path), dest_path)
            os.remove(str(src_path))
            if error_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Moved to {dest_folder}: {src_path.name} ({error_count} error(s))'
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Moved to {dest_folder}: {src_path.name}'
                    )
                )
        except PermissionError as perm_err:
            # Retry with delays
            for attempt in range(5):
                try:
                    delay = 2 * (attempt + 1)
                    logger.warning('File locked, retrying in %ds... (attempt %d/%d)', delay, attempt + 1, 5)
                    time.sleep(delay)
                    shutil.copy2(str(src_path), dest_path)
                    os.remove(str(src_path))
                    if error_count > 0:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Moved to {dest_folder} (after retry): {src_path.name} ({error_count} error(s))'
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Moved to {dest_folder} (after retry): {src_path.name}'
                            )
                        )
                    break
                except Exception as retry_err:
                    if attempt == 4:  # Last attempt
                        self.stderr.write(self.style.ERROR(f'Failed to move file after retries: {src_path.name}: {retry_err}'))
        except Exception as move_err:
            self.stderr.write(self.style.ERROR(f'Failed to move file: {move_err}'))

    def handle(self, *args, **options):
        # Use inbox_dir if provided, otherwise fall back to positional path, then to settings
        inbox_dir = options['inbox_dir'] or options.get('path') or str(getattr(settings, 'IMPORT_INBOX_DIR', Path('inbox')))
        target_path = Path(inbox_dir)
        dry_run = options['dry_run']

        # Setup file logging
        log_dir = Path(getattr(settings, 'IMPORT_LOGS_DIR', settings.BASE_DIR / 'logs'))
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file_path = log_dir / f'pdf_import_{timestamp}.log'
        term_logger = TerminalLogger(log_file_path)
        # Django's self.stdout is a separate object from sys.stdout,
        # so we replace it with our TerminalLogger to capture all output
        self.stdout = term_logger

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no database changes'))

        if target_path.is_file():
            pdf_files = [target_path]
            move_files = False  # Don't move when processing a single explicit file path
        elif target_path.is_dir():
            pdf_files = sorted(target_path.glob('*.pdf'))
            if not pdf_files:
                raise CommandError(f'No PDF files found in {target_path}')
            move_files = True  # Move files when batch processing from a directory
        else:
            raise CommandError(f'Path does not exist: {target_path}')

        files_processed = 0
        statements_created = 0
        transactions_imported = 0
        balances_recorded = 0
        duplicates_skipped = 0
        errors = 0

        # Initialize JSON output directory (used in summary)
        json_dir = Path(getattr(settings, 'IMPORT_JSON_DIR', settings.BASE_DIR / 'processed' / 'json_logs'))
        json_dir.mkdir(parents=True, exist_ok=True)

        for pdf_file in pdf_files:
            files_processed += 1
            self.stdout.write(f'Processing: {pdf_file.name}')

            try:
                parsed_list = parse_rbc_monthly_vl(str(pdf_file))
            except (VLParseError, ValueError) as e:
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
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Unexpected error parsing {pdf_file.name}: {e}'))
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

            # parsed_list is a list of dicts, one per currency section (CAD, USD, etc.)
            file_transactions_total = 0
            file_balances_total = 0
            file_statements_total = 0
            file_errors = []
            account = None
            all_parsed_data = {}  # For JSON output

            for parsed in parsed_list:
                statement_date = parsed.get('statement_date')
                account_number = parsed.get('account_number', '')
                currency = parsed.get('currency', 'CAD')

                if not statement_date:
                    error_msg = f'could not determine statement date'
                    file_errors.append({'currency': currency, 'error': error_msg})
                    self.stderr.write(self.style.ERROR(
                        f'Skipping {pdf_file.name} [{currency}]: {error_msg}'
                    ))
                    errors += 1
                    continue

                # Look up or create Account (once per file)
                if account is None:
                    if account_number:
                        # Normalize to 8-digit canonical form first
                        digits = re.sub(r'\D', '', account_number)
                        canonical = digits[:8] if len(digits) >= 8 else digits
                        try:
                            account = Account.objects.get(account_id=canonical)
                        except Account.DoesNotExist:
                            # Fallback: exact match on raw string
                            try:
                                account = Account.objects.get(account_id=account_number)
                            except Account.DoesNotExist:
                                # Auto-create account if not found
                                account = Account.objects.create(
                                    account_id=canonical,
                                    name=f'Auto-created from PDF: {account_number}',
                                    broker='RBC',
                                )
                                self.stdout.write(
                                    self.style.SUCCESS(
                                        f'Auto-created account: {account_number} -> {canonical}'
                                    )
                                )

                if account is None:
                    error_msg = f'No account number found in PDF [{currency}]'
                    file_errors.append({'currency': currency, 'error': error_msg})
                    self.stderr.write(self.style.ERROR(
                        f'Skipping {pdf_file.name} [{currency}]: {error_msg}'
                    ))
                    errors += 1
                    continue

                # Check for duplicate (per currency, excluding file_name to prevent duplicates on re-import)
                existing = RbcMonthly.objects.filter(
                    account=account,
                    statement_date=statement_date,
                    currency=currency,
                )
                if existing.exists():
                    existing_record = existing.first()
                    self.stdout.write(
                        self.style.WARNING(
                            f'Skipping duplicate: {pdf_file.name} [{currency}] '
                            f'(account={account}, date={statement_date}, '
                            f'existing_file={existing_record.file_name})'
                        )
                    )
                    duplicates_skipped += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Would create RbcMonthly [{currency}] for {account} on {statement_date} '
                            f'with {len(parsed["transactions"])} transactions '
                            f'and {len(parsed["balances"])} balances'
                        )
                    )
                    file_statements_total += 1
                    file_transactions_total += len(parsed['transactions'])
                    file_balances_total += len(parsed['balances'])
                    continue

                # Create RbcMonthly
                rbc_monthly = RbcMonthly.objects.create(
                    account=account,
                    file_name=pdf_file.name,
                    statement_date=statement_date,
                    currency=currency,
                    last_statement_date=parsed.get('last_statement_date'),
                    account_number=account_number,
                    fx_rate=parsed.get('fx_rate', Decimal('1.000000')),
                    begin_total_market_value=parsed.get('begin_total_market_value', Decimal('0.00')),
                    end_total_market_value=parsed.get('end_total_market_value', Decimal('0.00')),
                )

                # Create RbcMonthlyBalance records
                for bal in parsed.get('balances', []):
                    RbcMonthlyBalance.objects.create(
                        rbc_monthly=rbc_monthly,
                        currency=currency,
                        account_type=bal['account_type'],
                        opening_balance=bal.get('opening', Decimal('0.00')),
                        closing_balance=bal.get('closing', Decimal('0.00')),
                        market_value=bal.get('market_value', Decimal('0.00')),
                        percentage=bal.get('percentage', Decimal('0.00')),
                    )
                    file_balances_total += 1

                # Create RbcMonthlyTransaction records
                for tx in parsed.get('transactions', []):
                    RbcMonthlyTransaction.objects.create(
                        rbc_monthly=rbc_monthly,
                        date=tx['date'],
                        activity_type=tx['activity_type'],
                        description=tx['description'],
                        symbol=tx.get('symbol', ''),
                        quantity=tx.get('quantity') or Decimal('0.00'),
                        price=tx.get('price') or Decimal('0.00'),
                        debit=tx.get('debit') or Decimal('0.00'),
                        credit=tx.get('credit') or Decimal('0.00'),
                        currency=tx.get('currency', currency),
                        commission=tx.get('commission') or Decimal('0.00'),
                        notes=tx.get('notes', ''),
                    )
                    file_transactions_total += 1

                file_statements_total += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Imported [{currency}]: {pdf_file.name} -> {file_statements_total} statements, '
                        f'{file_transactions_total} transactions, {file_balances_total} balances'
                    )
                )

            # Save JSON output for this PDF
            json_filename = pdf_file.stem + '.json'
            json_path = json_dir / json_filename

            # Check if this is an Annual Report (no transactions, no balances, just text)
            is_annual_report = (file_statements_total == 0 and 
                                file_balances_total == 0 and 
                                file_transactions_total == 0)
            
            # Annual Reports are valid but have no account number - handle specially
            if is_annual_report:
                self.stdout.write(
                    self.style.WARNING(
                        f'Annual Report detected: {pdf_file.name} (no transactions/balances)'
                    )
                )

            json_data = {
                'filename': pdf_file.name,
                'processed_at': datetime.now().isoformat(),
                'is_annual_report': is_annual_report,
                'sections': [],
                'errors': file_errors,
            }

            for parsed in parsed_list:
                section_data = {
                    'currency': parsed.get('currency', 'CAD'),
                    'statement_date': str(parsed.get('statement_date')) if parsed.get('statement_date') else None,
                    'last_statement_date': str(parsed.get('last_statement_date')) if parsed.get('last_statement_date') else None,
                    'account_number': parsed.get('account_number', ''),
                    'fx_rate': str(parsed.get('fx_rate')) if parsed.get('fx_rate') is not None else None,
                    'begin_total_market_value': str(parsed.get('begin_total_market_value', 0)),
                    'end_total_market_value': str(parsed.get('end_total_market_value', 0)),
                    'balances_count': len(parsed.get('balances', [])),
                    'transactions_count': len(parsed.get('transactions', [])),
                    'income_summary': parsed.get('income_summary', {}),
                    'contribution_summary': parsed.get('contribution_summary', {}),
                    'is_annual_report_section': is_annual_report,
                    'balances': parsed.get('balances', []),
                    'transactions': parsed.get('transactions', []),
                }
                json_data['sections'].append(section_data)

            if file_errors:
                json_data['error'] = file_errors

            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, default=str)

            # Create FileImportLog entry (once per file)
            if not dry_run:
                # Any file with errors is an error, regardless of annual report status
                if file_errors:
                    log_status = 'error'
                    log_error_message = '; '.join([e['error'] for e in file_errors])
                else:
                    log_status = 'success'
                    log_error_message = ''
                
                FileImportLog.objects.create(
                    filename=pdf_file.name,
                    broker='RBC',
                    imported_at=timezone.now(),
                    records_imported=file_transactions_total,
                    records_skipped=0,
                    status=log_status,
                    error_message=log_error_message,
                )

            # Update global counters
            statements_created += file_statements_total
            transactions_imported += file_transactions_total
            balances_recorded += file_balances_total

            # Move files based on success/failure
            if not dry_run and move_files:
                processed_dir = str(getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed'))
                os.makedirs(processed_dir, exist_ok=True)
                move_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                name, ext = os.path.splitext(pdf_file.name)

                if not file_errors:
                    # Move PERFECT files to processed/
                    dest_path = os.path.join(processed_dir, f"{name}_{move_timestamp}{ext}")
                    self._move_file(pdf_file, dest_path, 'processed/')
                else:
                    # Move ERROR files to processed/errors/
                    errors_dir = os.path.join(processed_dir, 'errors')
                    os.makedirs(errors_dir, exist_ok=True)
                    dest_path = os.path.join(errors_dir, f"{name}_{move_timestamp}{ext}")
                    self._move_file(pdf_file, dest_path, 'processed/errors/', error_count=len(file_errors))

        # Summary
        self.stdout.write(self.style.SUCCESS(
            f'\n=== Import Summary ===\n'
            f'Files processed:    {files_processed}\n'
            f'Statements created: {statements_created}\n'
            f'Transactions imported: {transactions_imported}\n'
            f'Balances recorded:  {balances_recorded}\n'
            f'Duplicates skipped: {duplicates_skipped}\n'
            f'Errors:             {errors}\n'
            f'Log file:           {log_file_path}\n'
            f'JSON files:         {json_dir}\n'
        ))

       # Restore stdout and close log file
        original_stdout = term_logger.terminal
        term_logger.close()
        self.stdout = original_stdout
        sys.stdout = sys.__stdout__
