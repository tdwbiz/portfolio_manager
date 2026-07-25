"""Unified inbox import command.

Processes every PDF dropped in the inbox directory by:
  1. Classifying the document (annual report vs monthly statement) using the
     VL model — no filename assumptions.
  2. Routing to the appropriate VL parser.
  3. Importing records into the database.
  4. Saving a JSON log to processed/json_logs/.
  5. Moving the PDF to processed/ (success) or processed/errors/ (failure).

Usage examples:
    python manage.py import_inbox
    python manage.py import_inbox --inbox-dir "inbox/for test"
    python manage.py import_inbox --inbox-dir "inbox/for test/annual Report"
    python manage.py import_inbox --dry-run
"""

import json
import logging
import re
import shutil
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import (
    Account,
    FileImportLog,
    RbcAnnual,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
)
from imports.classify_pdf_vl import classify_pdf
from imports.parsers_annual_vl import parse_rbc_annual_report_vl
from imports.parsers_monthly_vl import parse_rbc_monthly_vl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        'Classify and import all PDFs from the inbox directory using the '
        'Qwen2.5-VL vision model.  Detects document type automatically — '
        'no filename convention required.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--inbox-dir',
            type=str,
            default=None,
            help='Path to the inbox directory (default: settings.IMPORT_INBOX_DIR).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Classify and parse PDFs but do not write to the database.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Stop after processing this many PDFs (useful for testing).',
        )

    # ------------------------------------------------------------------
    # handle
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        inbox_dir = (
            options['inbox_dir']
            or str(getattr(settings, 'IMPORT_INBOX_DIR', Path('inbox')))
        )
        target_path = Path(inbox_dir)
        dry_run = options['dry_run']
        limit = options.get('limit')  # None means no limit

        if not target_path.exists():
            raise CommandError(f'Inbox directory does not exist: {target_path}')

        # Resolve output directories from settings
        processed_dir = Path(
            getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed')
        )
        errors_dir = processed_dir / 'errors'
        json_dir = Path(
            getattr(settings, 'IMPORT_JSON_DIR', settings.BASE_DIR / 'processed' / 'json_logs')
        )

        for d in (processed_dir, errors_dir, json_dir):
            d.mkdir(parents=True, exist_ok=True)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE — no database changes'))

        # Collect PDFs (non-recursive — only direct children of inbox)
        pdf_files = sorted(target_path.glob('*.pdf'))
        if limit:
            pdf_files = pdf_files[:limit]
        if not pdf_files:
            self.stdout.write(self.style.WARNING(f'No PDF files found in {target_path}'))
            return

        self.stdout.write(f'Found {len(pdf_files)} PDF(s) in {target_path}')

        # Counters
        total = len(pdf_files)
        classified_annual = 0
        classified_monthly = 0
        classified_unknown = 0
        imported_annual = 0
        imported_monthly_stmts = 0
        imported_transactions = 0
        imported_balances = 0
        imported_holdings = 0
        duplicates = 0
        errors = 0

        for i, pdf_file in enumerate(pdf_files, 1):
            self.stdout.write(f'\n[{i}/{total}] {pdf_file.name}')

            # ── Step 1: classify ──────────────────────────────────────────
            self.stdout.write('  Classifying...')
            doc_type = classify_pdf(str(pdf_file))
            self.stdout.write(f'  -> {doc_type}')

            # ── Step 2: parse + import + log ──────────────────────────────
            if doc_type == 'annual':
                classified_annual += 1
                rec, errs, err_msg = self._process_annual(pdf_file, dry_run)
                imported_annual += rec
                errors += errs

                json_data = {'type': 'annual', 'filename': pdf_file.name,
                             'processed_at': datetime.now().isoformat(),
                             'records_created': rec, 'errors': errs,
                             'error_messages': [err_msg] if err_msg else []}

            elif doc_type == 'monthly':
                classified_monthly += 1
                stmts, txns, bals, holdings, errs, json_sections, err_msgs = self._process_monthly(
                    pdf_file, dry_run
                )
                imported_monthly_stmts += stmts
                imported_transactions += txns
                imported_balances += bals
                imported_holdings += holdings
                errors += errs

                json_data = {
                    'type': 'monthly',
                    'filename': pdf_file.name,
                    'processed_at': datetime.now().isoformat(),
                    'errors': errs,
                    'error_messages': err_msgs,
                }

            else:
                classified_unknown += 1
                errors += 1
                self.stderr.write(
                    self.style.ERROR(
                        f'  Cannot classify {pdf_file.name} — moving to errors/'
                    )
                )
                if not dry_run:
                    self._log_import(pdf_file.name, 0, 'error', 'Document type unknown')
                    json_data = {
                        'type': 'unknown',
                        'filename': pdf_file.name,
                        'processed_at': datetime.now().isoformat(),
                        'errors': 1,
                        'error_messages': [
                            'VL classifier could not identify document type. '
                            'Page 1 did not match RBC monthly statement or annual report. '
                            'Possible causes: different statement format, non-RBC document, '
                            'scanned image quality, or cover page before statement.'
                        ],
                    }
                    json_path = json_dir / f'{pdf_file.stem}_ERROR.json'
                    try:
                        with open(json_path, 'w', encoding='utf-8') as fh:
                            json.dump(json_data, fh, indent=2, default=str)
                    except OSError as exc:
                        logger.warning("Could not write JSON log for %s: %s", pdf_file.name, exc)
                    dest = errors_dir / pdf_file.name
                    _move_file(pdf_file, dest)
                continue

            # ── Step 3: save JSON log (always) ──────────────────────────
            if not dry_run:
                json_filename = pdf_file.stem + ('_ERROR' if errs > 0 else '')
                json_path = json_dir / f'{json_filename}.json'
                try:
                    with open(json_path, 'w', encoding='utf-8') as fh:
                        json.dump(json_data, fh, indent=2, default=str)
                except OSError as exc:
                    logger.warning("Could not write JSON log for %s: %s", pdf_file.name, exc)

            # ── Step 4: move PDF ──────────────────────────────────────────
            if not dry_run:
                had_error = errs > 0
                if had_error:
                    dest = errors_dir / pdf_file.name
                    moved = _move_file(pdf_file, dest)
                    if moved:
                        self.stdout.write(
                            self.style.WARNING(f'  Moved to errors/: {pdf_file.name}')
                        )
                else:
                    dest = processed_dir / pdf_file.name
                    moved = _move_file(pdf_file, dest)
                    if moved:
                        self.stdout.write(
                            self.style.SUCCESS(f'  Moved to processed/: {pdf_file.name}')
                        )

        # ── Final summary ──────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(
            f'\n=== Inbox Import Summary ===\n'
            f'PDFs found:              {total}\n'
            f'  -> annual reports:      {classified_annual}\n'
            f'  -> monthly statements:  {classified_monthly}\n'
            f'  -> unknown:             {classified_unknown}\n'
            f'Annual records created:  {imported_annual}\n'
            f'Monthly statements:      {imported_monthly_stmts}\n'
            f'Transactions imported:   {imported_transactions}\n'
            f'Balances recorded:       {imported_balances}\n'
            f'Holdings recorded:       {imported_holdings}\n'
            f'Duplicates skipped:      {duplicates}\n'
            f'Errors:                  {errors}\n'
            f'JSON logs:               {json_dir}\n'
        ))

    # ------------------------------------------------------------------
    # Annual processing
    # ------------------------------------------------------------------

    def _process_annual(self, pdf_file: Path, dry_run: bool) -> tuple[int, int, str]:
        """Parse and import an annual report PDF.

        Returns (records_created, error_count, error_message).
        """
        try:
            parsed = parse_rbc_annual_report_vl(str(pdf_file))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'  Parse error: {exc}'))
            if not dry_run:
                self._log_import(pdf_file.name, 0, 'error', str(exc))
            return 0, 1, str(exc)

        if dry_run:
            currencies = [c for c in ('cad', 'usd') if parsed.get(c) is not None]
            self.stdout.write(
                self.style.SUCCESS(
                    f'  [DRY-RUN] Would import annual: {parsed.get("account_number")} '
                    f'year={parsed["report_year"]} currencies={currencies}'
                )
            )
            return len(currencies), 0, ''

        account = _get_or_create_account(
            parsed['account_number'],
            parsed.get('account_type_code', ''),
        )

        records_created = 0
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
                        f'  Duplicate annual: {pdf_file.name} '
                        f'(year={parsed["report_year"]}, {currency})'
                    )
                )
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
            records_created += 1

        if records_created > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f'  Imported annual: {pdf_file.name} -> {records_created} record(s)'
                )
            )
            self._log_import(pdf_file.name, records_created, 'ok', '')
        else:
            self.stdout.write(
                self.style.WARNING(f'  All duplicates: {pdf_file.name}')
            )
            self._log_import(pdf_file.name, 0, 'ok', 'All records already exist')

        return records_created, 0, ''

    # ------------------------------------------------------------------
    # Monthly processing
    # ------------------------------------------------------------------

    def _process_monthly(
        self, pdf_file: Path, dry_run: bool
    ) -> tuple[int, int, int, int, int, list, list]:
        """Parse and import a monthly statement PDF.

        Returns (statements_created, transactions_imported, balances_recorded,
                 holdings_recorded, error_count, json_sections, error_messages).
        """
        try:
            parsed_list = parse_rbc_monthly_vl(str(pdf_file))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'  Parse error: {exc}'))
            if not dry_run:
                self._log_import(pdf_file.name, 0, 'error', str(exc))
            return 0, 0, 0, 0, 1, [], [str(exc)]

        if parsed_list is None:
            empty_msg = 'Parser returned no sections (possible unsupported statement format)'
            if dry_run:
                self.stderr.write(self.style.WARNING(f'  [DRY-RUN] {empty_msg}'))
                return 0, 0, 0, 0, 1, [], [empty_msg]
            return 0, 0, 0, 0, 1, [], [empty_msg]

        # parsed_list is a list of dicts, one per currency section (CAD, USD, etc.)
        account = None
        stmts_created = 0
        txns_imported = 0
        bals_imported = 0
        holdings_imported = 0
        file_errors = []
        json_sections = []

        for parsed in parsed_list:
            statement_date = parsed.get('statement_date')
            account_number = parsed.get('account_number', '')
            currency = parsed.get('currency', 'CAD')

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  [DRY-RUN] Would import monthly [{currency}]: '
                        f'{account_number} {statement_date} '
                        f'\u2014 {len(parsed.get("transactions", []))} txns, '
                        f'{len(parsed.get("holdings", []))} holdings, '
                        f'{len(parsed.get("balances", []))} balances'
                    )
                )
                stmts_created += 1
                continue

            if not statement_date:
                msg = 'Parser did not return statement_date'
                file_errors.append({'currency': currency, 'error': msg})
                self.stderr.write(self.style.ERROR(f'  Skipping [{currency}]: {msg}'))
                continue

            if account is None:
                account = _get_or_create_account(account_number, '')
                if account is None:
                    msg = f'Cannot resolve account for account_number={account_number!r}'
                    file_errors.append({'currency': currency, 'error': msg})
                    self.stderr.write(self.style.ERROR(f'  Skipping [{currency}]: {msg}'))
                    continue

            # Duplicate check
            existing = RbcMonthly.objects.filter(
                account=account,
                statement_date=statement_date,
                currency=currency,
                file_name=pdf_file.name,
            )
            if existing.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f'  Duplicate monthly [{currency}]: {pdf_file.name} '
                        f'(account={account}, date={statement_date})'
                    )
                )
                continue

            rbc_monthly = RbcMonthly.objects.create(
                account=account,
                file_name=pdf_file.name,
                statement_date=statement_date,
                currency=currency,
                account_number=account_number,
                begin_total_market_value=parsed.get('begin_total_market_value', Decimal('0.00')),
                end_total_market_value=parsed.get('end_total_market_value', Decimal('0.00')),
                fx_rate=parsed.get('fx_rate', Decimal('1.0000')),
            )

            # Bulk create balances
            balances_to_create = [
                RbcMonthlyBalance(
                    rbc_monthly=rbc_monthly,
                    currency=currency,
                    account_type=bal['account_type'],
                    opening_balance=bal.get('opening', Decimal('0.00')),
                    closing_balance=bal.get('closing', Decimal('0.00')),
                    market_value=bal.get('market_value', Decimal('0.00')),
                    percentage=bal.get('percentage', Decimal('0.00')),
                )
                for bal in parsed.get('balances', [])
            ]
            if balances_to_create:
                RbcMonthlyBalance.objects.bulk_create(balances_to_create)
            bals_imported += len(balances_to_create)

            # Bulk create holdings
            holdings_to_create = [
                RbcMonthlyHolding(
                    rbc_monthly=rbc_monthly,
                    currency=currency,
                    security=h.get('description', ''),
                    symbol=h.get('symbol', '')[:200],
                    quantity=h.get('quantity', Decimal('0.0000')),
                    book_cost=h.get('book_cost', Decimal('0.00')),
                    market_price=h.get('market_price', Decimal('0.0000')),
                    market_value=h.get('market_value', Decimal('0.00')),
                )
                for h in parsed.get('holdings', [])
            ]
            if holdings_to_create:
                RbcMonthlyHolding.objects.bulk_create(holdings_to_create)
            holdings_imported += len(holdings_to_create)

            # Bulk create transactions
            txs_to_create = [
                RbcMonthlyTransaction(
                    rbc_monthly=rbc_monthly,
                    date=tx['date'],
                    activity_type=tx['activity_type'][:100],
                    description=tx['description'][:500],
                    symbol=(tx.get('symbol', '') or '')[:200],
                    quantity=tx.get('quantity') or Decimal('0.0000'),
                    price=tx.get('price') or Decimal('0.0000'),
                    debit=tx.get('debit') or Decimal('0.00'),
                    credit=tx.get('credit') or Decimal('0.00'),
                    currency=currency,
                )
                for tx in parsed.get('transactions', [])
            ]
            if txs_to_create:
                RbcMonthlyTransaction.objects.bulk_create(txs_to_create)
            txns_imported += len(txs_to_create)

            stmts_created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'  Imported monthly [{currency}]: {account_number} {statement_date} '
                    f'\u2014 {len(txs_to_create)} txns, '
                    f'{len(holdings_to_create)} holdings, '
                    f'{len(balances_to_create)} balances'
                )
            )

        if dry_run:
            return stmts_created, 0, 0, 0, 0, [], []

        if not dry_run and stmts_created > 0:
            self._log_import(
                pdf_file.name,
                stmts_created + txns_imported + bals_imported + holdings_imported,
                'success',
                '',
            )

        return stmts_created, txns_imported, bals_imported, holdings_imported, len(file_errors), json_sections, [e['error'] for e in file_errors]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_import(self, filename: str, records: int, status: str, error_msg: str):
        """Create a FileImportLog entry."""
        FileImportLog.objects.create(
            filename=filename,
            broker='RBC',
            imported_at=timezone.now(),
            records_imported=records,
            records_skipped=0,
            status=status,
            error_message=error_msg,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _normalize_rbc_account_id(raw: str) -> str:
    """Extract the 8-digit canonical RBC account ID from any format.

    Handles: '900-00003-1-8' -> '90000003', '9000000318' -> '90000003',
             '90000003' -> '90000003'.
    """
    digits = re.sub(r'\D', '', raw)
    return digits[:8] if len(digits) >= 8 else digits


def _get_or_create_account(account_number: str, account_type_code: str) -> Account:
    """Look up an existing Account by number, or auto-create one."""
    if account_number:
        # Normalize to 8-digit canonical form first
        canonical = _normalize_rbc_account_id(account_number)
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
    account_id = _normalize_rbc_account_id(account_number) if account_number else f'RBC-AUTO-{datetime.now().strftime("%H%M%S")}'
    name = f'Auto-created from PDF: {account_number}'
    account = Account.objects.create(
        account_id=account_id,
        name=name,
        broker='RBC',
    )
    logger.warning("Auto-created account for unrecognised number %r — assign to correct account manually", account_number)
    return account


def _move_file(src: Path, dest: Path) -> bool:
    """Move src to dest, retrying up to 5 times on PermissionError.

    Returns True if the file was moved, False otherwise.
    If the source file is already gone (moved by a concurrent process),
    logs a warning and returns False without raising.
    """
    if not src.exists():
        logger.warning("_move_file: %s not found — already moved by another process", src.name)
        return False
    for attempt in range(5):
        try:
            shutil.move(str(src), str(dest))
            logger.info("Moved %s -> %s", src.name, dest)
            return True
        except PermissionError:
            if attempt == 4:
                logger.error("Failed to move %s after 5 attempts", src.name)
                return False
            delay = 2 * (attempt + 1)
            logger.warning("File locked, retrying move in %ds...", delay)
            time.sleep(delay)
        except FileNotFoundError:
            logger.warning("_move_file: %s disappeared during move — already moved by another process", src.name)
            return False
        except Exception as exc:
            logger.error("Cannot move %s: %s", src.name, exc)
            return False
    return False

