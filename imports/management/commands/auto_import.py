"""Django management command to auto-import broker files (CSVs and PDFs) from the inbox folder."""

import hashlib
import logging
import os
import shutil
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Dict

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Account, BrokerStatement, FileImportLog, Transaction, TransactionType, compute_tx_hash
from core.models import RbcAnnual
from imports.parsers import parse_ib_csv, parse_rbc_csv
from imports.parsers_monthly_vl import parse_rbc_monthly_vl, VLParseError
from imports.parsers_annual_vl import parse_rbc_annual_report_vl
from imports.rejection_log import rejection_from_trade, write_rejection_log

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Scan inbox/ for CSV files and auto-import broker transactions.'

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            '--broker',
            type=str,
            choices=['IB', 'RBC'],
            default=None,
            help='Override broker detection (IB or RBC)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to DB or moving files',
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_broker(self, file_path: str) -> str:
        """Detect broker type from file content (not filename)."""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read(4096)  # read first 4KB — enough for headers

        if 'Activity Export' in content or '"Activity"' in content:
            return 'RBC'
        if 'Transaction History' in content or 'Statement' in content:
            return 'IB'
        return 'IB'  # default

    def _move_file(self, src: str, dest_dir: str) -> str:
        """Atomic copy-then-delete to dest_dir, appending a timestamp."""
        os.makedirs(dest_dir, exist_ok=True)
        name, ext = os.path.splitext(os.path.basename(src))
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        dest_path = os.path.join(dest_dir, f"{name}_{timestamp}{ext}")
        shutil.copy2(src, dest_path)
        os.remove(src)
        return dest_path

    # ------------------------------------------------------------------
    # Main handler
    # ------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        broker_override: str | None = options['broker']
        dry_run: bool = options['dry_run']

        inbox_dir = str(getattr(settings, 'IMPORT_INBOX_DIR', settings.BASE_DIR / 'inbox'))
        processed_dir = str(getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed'))
        error_dir = os.path.join(processed_dir, 'errors')

        if not os.path.exists(inbox_dir):
            self.stdout.write(self.style.ERROR(f"Inbox directory not found: {inbox_dir}"))
            return

        csv_files = sorted(f for f in os.listdir(inbox_dir) if f.lower().endswith('.csv'))
        pdf_files = sorted(f for f in os.listdir(inbox_dir) if f.lower().endswith('.pdf'))
        if not csv_files and not pdf_files:
            self.stdout.write("No CSV or PDF files found in inbox.")
            return

        total_imported = total_skipped = total_files = 0

        for filename in csv_files:
            file_path = os.path.join(inbox_dir, filename)
            total_files += 1
            broker = broker_override or 'UNK'

            try:
                # Hash check — skip if identical content was imported before
                with open(file_path, 'rb') as _fh:
                    content_hash = hashlib.sha256(_fh.read()).hexdigest()
                prior = FileImportLog.objects.filter(content_hash=content_hash).first()
                if prior and not dry_run:
                    self.stdout.write(
                        f"  Skipping {filename}: identical content already imported as {prior.filename}"
                    )
                    logger.info(
                        f"auto_import: skipping {filename} — duplicate of {prior.filename}"
                    )
                    # Move the duplicate out of inbox without creating a new log
                    self._move_file(file_path, processed_dir)
                    total_files -= 1  # don't count it as processed
                    continue

                # Detect broker
                broker = broker_override or self._detect_broker(file_path)

                # Parse
                exchange_rate_cad: Decimal = Decimal('1.000000')
                rejections: List[Dict[str, Any]] = []
                if broker == 'RBC':
                    trades, exchange_rate_cad = parse_rbc_csv(file_path, rejections)
                else:
                    trades = parse_ib_csv(file_path, rejections)

                if dry_run:
                    self.stdout.write(
                        f"[DRY RUN] {filename} ({broker}): {len(trades)} trades detected"
                    )
                    continue

                # Import
                imported_count = skipped_count = 0

                with transaction.atomic():
                    statement = BrokerStatement.objects.create(
                        source=broker,
                        file_date=timezone.now().date(),
                        raw_filename=filename,
                    )

                    for trade in trades:
                        account_id = trade.get('account_id', '').strip().upper()
                        if account_id:
                            account, _ = Account.objects.get_or_create(
                                account_id=account_id,
                                defaults={'name': account_id, 'broker': broker},
                            )
                        else:
                            account = None

                        # Ensure TransactionType record exists
                        TransactionType.objects.get_or_create(
                            code=trade['type'],
                            defaults={'label': trade.get('type_label', trade['type'])},
                        )

                        # Dedup via content hash (broker+account+date+symbol+type+qty+price+amount+currency)
                        tx_hash = compute_tx_hash(
                            broker,
                            account.account_id if account else '',
                            trade['date'],
                            trade['symbol'],
                            trade['type'],
                            trade['quantity'],
                            trade['price'],
                            trade['amount'],
                            trade.get('currency', 'USD'),
                        )
                        if Transaction.objects.filter(tx_hash=tx_hash).exists():
                            skipped_count += 1
                            rejections.append(rejection_from_trade(
                                filename, 0, 'Duplicate (already imported)', trade,
                            ))
                            continue

                        rate = trade.get('exchange_rate_cad', exchange_rate_cad)
                        if trade.get('currency', 'USD') == 'CAD':
                            amount_cad = trade['amount']
                        else:
                            amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))

                        Transaction.objects.create(
                            statement=statement,
                            account=account,
                            date=trade['date'],
                            symbol=trade['symbol'],
                            type=trade['type'],
                            quantity=trade['quantity'],
                            price=trade['price'],
                            commission=trade.get('commission', Decimal('0.00')),
                            amount=trade['amount'],
                            currency=trade.get('currency', 'USD'),
                            exchange_rate_cad=rate,
                            amount_cad=amount_cad,
                            notes=trade.get('notes', ''),
                            tx_hash=tx_hash,
                        )
                        imported_count += 1

                    if imported_count == 0:
                        statement.delete()

                status = 'success' if imported_count > 0 else 'partial'
                FileImportLog.objects.create(
                    filename=filename,
                    broker=broker,
                    records_imported=imported_count,
                    records_skipped=skipped_count,
                    status=status,
                    content_hash=content_hash,
                )

                dest = self._move_file(file_path, processed_dir)
                total_imported += imported_count
                total_skipped += skipped_count

                # Write rejection log to the configured logs directory
                log_path = write_rejection_log(rejections, str(settings.IMPORT_LOGS_DIR), filename)
                if log_path:
                    self.stdout.write(f"  Rejection log: {log_path}")

                logger.info(
                    f"auto_import: {filename} ({broker}) - "
                    f"{imported_count} imported, {skipped_count} skipped -> {dest}"
                )
                self.stdout.write(
                    f"  {filename} ({broker}): {imported_count} imported, {skipped_count} skipped"
                )

            except Exception as exc:
                FileImportLog.objects.create(
                    filename=filename,
                    broker=(broker_override or 'UNK')[:3],
                    status='error',
                    error_message=str(exc),
                )
                try:
                    self._move_file(file_path, error_dir)
                except Exception as move_err:
                    logger.error(f"auto_import: failed to move error file {filename}: {move_err}")

                logger.error(f"auto_import: error processing {filename}: {exc}")
                self.stdout.write(self.style.ERROR(f"  {filename}: ERROR — {exc}"))

        # --- PDF Processing ---
        if pdf_files:
            self.stdout.write(f"\nProcessing {len(pdf_files)} PDF file(s)...")
            for filename in pdf_files:
                file_path = os.path.join(inbox_dir, filename)
                total_files += 1
                try:
                    # Hash check
                    with open(file_path, 'rb') as _fh:
                        content_hash = hashlib.sha256(_fh.read()).hexdigest()
                    prior = FileImportLog.objects.filter(content_hash=content_hash).first()
                    if prior and not dry_run:
                        self.stdout.write(
                            f"  Skipping {filename}: identical content already imported as {prior.filename}"
                        )
                        logger.info(f"auto_import: skipping {filename} — duplicate of {prior.filename}")
                        if not dry_run:
                            self._move_file(file_path, processed_dir)
                        continue

                    # Detect PDF type: Annual Report vs Statement
                    is_annual = filename.lower().startswith('annual report')

                    if is_annual:
                        self.stdout.write(f"  Detected Annual Report: {filename}")
                        parsed = parse_rbc_annual_report_vl(file_path)

                        if dry_run:
                            currencies = [c for c in ('cad', 'usd') if parsed.get(c) is not None]
                            self.stdout.write(
                                f"  [DRY RUN] {filename}: report_year={parsed.get('report_year')}, "
                                f"account={parsed.get('account_number')}, "
                                f"currencies={currencies}"
                            )
                            continue

                        # Look up or create account
                        account_number = parsed.get('account_number', '')
                        account_type_code = parsed.get('account_type_code', '')

                        if account_number:
                            try:
                                account = Account.objects.get(account_id=account_number)
                            except Account.DoesNotExist:
                                partial = account_number.replace('-', '')
                                account = None
                                for acct in Account.objects.filter(broker='RBC'):
                                    if partial in acct.account_id.replace('-', ''):
                                        account = acct
                                        break
                                if account is None:
                                    account = Account.objects.create(
                                        account_id=account_number,
                                        name=f'RBC {account_type_code} {account_number}',
                                        broker='RBC',
                                    )
                        else:
                            account = None

                        if account is None:
                            raise ValueError(f'Could not find or create account for annual report: {filename}')

                        records_saved = 0
                        with transaction.atomic():
                            for currency, cdata in [('CAD', parsed.get('cad')), ('USD', parsed.get('usd'))]:
                                if cdata is None:
                                    continue
                                existing = RbcAnnual.objects.filter(
                                    account=account,
                                    report_year=parsed['report_year'],
                                    currency=currency,
                                )
                                if existing.exists():
                                    logger.info(f"auto_import: skipping duplicate RbcAnnual {account} {parsed['report_year']} {currency}")
                                    continue
                                RbcAnnual.objects.create(
                                    account=account,
                                    file_name=filename,
                                    report_year=parsed['report_year'],
                                    report_period_start=parsed['report_period_start'],
                                    report_period_end=parsed['report_period_end'],
                                    account_number=account_number,
                                    account_type_code=account_type_code,
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
                                records_saved += 1

                        FileImportLog.objects.create(
                            filename=filename,
                            broker='RBC',
                            records_imported=records_saved,
                            records_skipped=0,
                            status='ok',
                            content_hash=content_hash,
                        )

                        if not dry_run:
                            self._move_file(file_path, processed_dir)

                        total_imported += records_saved

                        self.stdout.write(f"  {filename}: {records_saved} RbcAnnual record(s) created")
                        logger.info(
                            f"auto_import: {filename} (Annual Report) - "
                            f"{annual_balances_recorded} balances, {annual_performance_recorded} performance, "
                            f"{annual_charges_recorded} charges, {annual_compensation_recorded} compensation"
                        )
                    else:
                        # Standard RBC PDF statement — use VL parser
                        self.stdout.write(f"  Parsing: {filename}")
                        try:
                            parsed_list = parse_rbc_monthly_vl(file_path)
                        except (VLParseError, ValueError) as e:
                            raise ValueError(f'VL parse failed: {e}')

                        if dry_run:
                            currencies = [s.get('currency', 'CAD') for s in parsed_list]
                            total_txns = sum(len(s.get('transactions', [])) for s in parsed_list)
                            total_bals = sum(len(s.get('balances', [])) for s in parsed_list)
                            self.stdout.write(
                                f"  [DRY RUN] {filename}: {len(parsed_list)} section(s) [{', '.join(currencies)}], "
                                f"{total_txns} transactions, {total_bals} balances"
                            )
                            continue

                        # Import PDF data — VL parser returns list of sections (one per currency)
                        statements_created = 0
                        transactions_imported = 0
                        balances_recorded = 0
                        duplicates_skipped = 0
                        account = None

                        for section in parsed_list:
                            currency = section.get('currency', 'CAD')

                            # Create account once (use first section's account number)
                            if account is None:
                                account_id = section.get('account_number', '').replace('-', '').upper()
                                if account_id:
                                    account, _ = Account.objects.get_or_create(
                                        account_id=account_id,
                                        defaults={'name': account_id, 'broker': 'RBC'},
                                    )
                                else:
                                    account = None

                            if account is None:
                                continue

                            # Create statement per section
                            statement = BrokerStatement.objects.create(
                                source='RBC',
                                file_date=timezone.now().date(),
                                raw_filename=filename,
                            )

                            # Import transactions
                            for tx_data in section.get('transactions', []):
                                tx_hash = compute_tx_hash(
                                    'RBC',
                                    account.account_id,
                                    tx_data['date'],
                                    tx_data['symbol'],
                                    tx_data['type'],
                                    tx_data['quantity'],
                                    tx_data['price'],
                                    tx_data['amount'],
                                    currency,
                                )
                                if Transaction.objects.filter(tx_hash=tx_hash).exists():
                                    duplicates_skipped += 1
                                    continue

                                Transaction.objects.create(
                                    statement=statement,
                                    account=account,
                                    date=tx_data['date'],
                                    symbol=tx_data['symbol'],
                                    type=tx_data['type'],
                                    quantity=tx_data['quantity'],
                                    price=tx_data['price'],
                                    commission=tx_data.get('commission', Decimal('0.00')),
                                    amount=tx_data['amount'],
                                    currency=currency,
                                    exchange_rate_cad=section.get('fx_rate', Decimal('1.000000')),
                                    amount_cad=tx_data.get('amount_cad', tx_data['amount']),
                                    notes=tx_data.get('notes', ''),
                                    tx_hash=tx_hash,
                                )
                                transactions_imported += 1

                            if transactions_imported > 0:
                                statements_created += 1

                            # Record balances
                            for bal_data in section.get('balances', []):
                                with transaction.atomic():
                                    balance_hash = hashlib.sha256(
                                        f"{filename}:{bal_data['account_type']}:{bal_data['closing']}".encode()
                                    ).hexdigest()
                                    if not FileImportLog.objects.filter(content_hash=balance_hash).exists():
                                        FileImportLog.objects.create(
                                            filename=f"{filename}_balance_{bal_data['account_type']}",
                                            broker='RBC',
                                            records_imported=1,
                                            records_skipped=0,
                                            status='success',
                                            content_hash=balance_hash,
                                        )
                                        balances_recorded += 1

                        status = 'success' if statements_created > 0 else 'partial'
                        FileImportLog.objects.create(
                            filename=filename,
                            broker='RBC',
                            records_imported=transactions_imported,
                            records_skipped=duplicates_skipped,
                            status=status,
                            content_hash=content_hash,
                        )

                        if not dry_run:
                            self._move_file(file_path, processed_dir)

                        total_imported += transactions_imported
                        total_skipped += duplicates_skipped

                        self.stdout.write(
                            f"  {filename}: {statements_created} statements, "
                            f"{transactions_imported} transactions, "
                            f"{balances_recorded} balances, {duplicates_skipped} skipped"
                        )
                        logger.info(
                            f"auto_import: {filename} (PDF) - "
                            f"{transactions_imported} imported, {duplicates_skipped} skipped"
                        )

                except Exception as exc:
                    FileImportLog.objects.create(
                        filename=filename,
                        broker='RBC',
                        status='error',
                        error_message=str(exc),
                    )
                    try:
                        self._move_file(file_path, error_dir)
                    except Exception as move_err:
                        logger.error(f"auto_import: failed to move error file {filename}: {move_err}")
                    logger.error(f"auto_import: error processing {filename}: {exc}")
                    self.stdout.write(self.style.ERROR(f"  {filename}: ERROR — {exc}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone: {total_files} file(s) processed, "
                f"{total_imported} imported, {total_skipped} skipped."
            ))
