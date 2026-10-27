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
from datetime import date as date_type
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
from core.utils.account_fingerprint import (
    build_fingerprint_name_map,
    fingerprint_account_number,
    lookup_account,
    normalize_account_number,
    scrub_text,
)
from core.utils.import_checks import content_hash_of, is_content_imported
from core.utils.safe_names import (
    ANNUAL,
    MONTHLY,
    UNKNOWN,
    is_safe_file_name,
    safe_file_name,
)
from imports.classify_pdf_vl import classify_pdf
from imports.parsers_annual_vl import parse_rbc_annual_report_vl
from imports.parsers_monthly_vl import parse_rbc_monthly_vl
from imports.parsers_pdf import (
    _extract_text_from_pdf,
    parse_rbc_pdf_multi,
    self_check_sections,
)

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

        # One query for the fingerprint->name map; scrub_text must never
        # query per candidate (SQL Server without MARS).
        name_map = build_fingerprint_name_map()

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
            # Content-based hash: the original file name carries the account
            # number and must never appear in stdout, logs or new names.
            full_hash = content_hash_of(pdf_file)
            h8 = full_hash[:8]
            self.stdout.write(f'\n[{i}/{total}] {h8}.pdf')

            # ── Step 0: file-level duplicate check (step 13b) ────────────
            # The same content already imported successfully → skip before
            # classify: no VL call, nothing stored. The PDF moves to
            # processed/ under a uniquified safe name; the JSON log carries
            # verdict "duplicate" and a FileImportLog row is written.
            if not dry_run and is_content_imported(full_hash):
                prior = FileImportLog.objects.filter(
                    content_hash=full_hash, status__in=('success', 'ok')
                ).order_by('-imported_at').first()
                base_name = (
                    prior.filename
                    if prior and is_safe_file_name(prior.filename)
                    else _unattributable_safe_name(pdf_file.name, h8)
                )
                safe_name = _unique_destination(processed_dir, base_name).name
                self.stdout.write(self.style.SUCCESS(
                    f'  Duplicate content — already imported as {base_name}; '
                    'skipping (no classify, no VL call)'
                ))
                self._log_import(safe_name, 0, 'duplicate',
                                 'Content already imported',
                                 content_hash=full_hash)
                json_data = {
                    'type': 'duplicate',
                    'filename': safe_name,
                    'processed_at': timezone.now().isoformat(),
                    'verdict': 'duplicate',
                    'errors': 0,
                    'error_messages': [],
                }
                json_path = _json_log_path(json_dir, safe_name, has_error=False)
                try:
                    with open(json_path, 'w', encoding='utf-8') as fh:
                        fh.write(_scrubbed_json(json_data, name_map))
                except OSError as exc:
                    logger.warning("Could not write JSON log for %s: %s", safe_name, exc)
                _move_file(pdf_file, processed_dir / safe_name, h8)
                duplicates += 1
                continue

            # ── Step 1: classify ──────────────────────────────────────────
            self.stdout.write('  Classifying...')
            doc_type = classify_pdf(str(pdf_file))
            self.stdout.write(f'  -> {doc_type}')

            # ── Step 2: parse + import + log ──────────────────────────────
            if doc_type == 'annual':
                classified_annual += 1
                (rec, errs, err_msg, annual_data, safe_name,
                 verdict) = self._process_annual(pdf_file, dry_run, h8, full_hash)
                imported_annual += rec
                errors += errs
                if verdict == 'duplicate':
                    duplicates += 1

                json_data = {'type': 'annual', 'filename': safe_name,
                             'processed_at': timezone.now().isoformat(),
                             'verdict': verdict,
                             'records_created': rec, 'errors': errs,
                             'error_messages': [err_msg] if err_msg else [],
                             'data': annual_data}

            elif doc_type == 'monthly':
                classified_monthly += 1
                (stmts, txns, bals, holdings, errs, json_sections, err_msgs,
                 safe_name, parser_used, self_check_reasons, verdict) = (
                    self._process_monthly(pdf_file, dry_run, name_map, h8, full_hash)
                )
                imported_monthly_stmts += stmts
                imported_transactions += txns
                imported_balances += bals
                imported_holdings += holdings
                errors += errs
                if verdict == 'duplicate':
                    duplicates += 1

                json_data = {
                    'type': 'monthly',
                    'filename': safe_name,
                    'processed_at': timezone.now().isoformat(),
                    'parser': parser_used,
                    'self_check_reasons': self_check_reasons,
                    'verdict': verdict,
                    'errors': errs,
                    'error_messages': err_msgs,
                    'sections': json_sections,
                }

            else:
                classified_unknown += 1
                errors += 1
                # Cannot be attributed to an account (or even a type):
                # safe name with UNKNOWN_ACCOUNT and today's date.
                safe_name = None if dry_run else safe_file_name(
                    pdf_file.name, 'UNKNOWN_ACCOUNT', UNKNOWN, hash8=h8
                )
                self.stderr.write(
                    self.style.ERROR(
                        f'  Cannot classify {h8}.pdf — moving to errors/'
                    )
                )
                if not dry_run:
                    self._log_import(safe_name, 0, 'error', 'Document type unknown',
                                     content_hash=full_hash)
                    json_data = {
                        'type': 'unknown',
                        'filename': safe_name,
                        'processed_at': timezone.now().isoformat(),
                        'verdict': 'error',
                        'errors': 1,
                        'error_messages': (
                            'VL classifier could not identify document type. '
                            'Page 1 did not match RBC monthly statement or annual report. '
                            'Possible causes: different statement format, non-RBC document, '
                            'scanned image quality, or cover page before statement.',
                        ),
                    }
                    json_path = _json_log_path(json_dir, safe_name, has_error=True)
                    try:
                        with open(json_path, 'w', encoding='utf-8') as fh:
                            fh.write(_scrubbed_json(json_data, name_map))
                    except OSError as exc:
                        logger.warning("Could not write JSON log for %s: %s", safe_name, exc)
                    dest = errors_dir / safe_name
                    _move_file(pdf_file, dest, h8)
                continue

            # ── Step 3: save JSON log (always) ──────────────────────────
            if not dry_run:
                json_path = _json_log_path(json_dir, safe_name, has_error=errs > 0)
                try:
                    with open(json_path, 'w', encoding='utf-8') as fh:
                        fh.write(_scrubbed_json(json_data, name_map))
                except OSError as exc:
                    logger.warning("Could not write JSON log for %s: %s", safe_name, exc)

            # ── Step 4: move PDF ──────────────────────────────────────────
            if not dry_run:
                had_error = errs > 0
                dest_dir = errors_dir if had_error else processed_dir
                dest = dest_dir / safe_name
                moved = _move_file(pdf_file, dest, h8)
                if moved:
                    label = 'errors/' if had_error else 'processed/'
                    line = f'  Moved to {label}: {safe_name}'
                    if had_error:
                        self.stdout.write(self.style.WARNING(line))
                    else:
                        self.stdout.write(self.style.SUCCESS(line))

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

    def _process_annual(
        self, pdf_file: Path, dry_run: bool, hash8: str, full_hash: str
    ) -> tuple[int, int, str, dict, str | None, str]:
        """Parse and import an annual report PDF.

        Returns (records_created, error_count, error_message, parsed_data,
                 safe_name, verdict). ``safe_name`` is None in dry-run mode.
        """
        try:
            parsed = parse_rbc_annual_report_vl(str(pdf_file))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'  Parse error: {exc}'))
            if not dry_run:
                safe_name = _unattributable_safe_name(pdf_file.name, hash8)
                self._log_import(safe_name, 0, 'error', str(exc),
                                 content_hash=full_hash)
                return 0, 1, str(exc), {}, safe_name, 'error'
            return 0, 1, str(exc), {}, None, 'error'

        if dry_run:
            currencies = [c for c in ('cad', 'usd') if parsed.get(c) is not None]
            acct = lookup_account(parsed.get('account_number'))
            self.stdout.write(
                self.style.SUCCESS(
                    f'  [DRY-RUN] Would import annual: '
                    f'{acct.name if acct else "UNKNOWN_ACCOUNT"} '
                    f'year={parsed["report_year"]} currencies={currencies}'
                )
            )
            return len(currencies), 0, '', parsed, None, 'imported'

        account = _get_or_create_account(
            parsed['account_number'],
            parsed.get('account_type_code', ''),
        )
        if account is None:
            msg = 'Unknown account fingerprint — file rejected (run set_account_fingerprint first)'
            self.stderr.write(self.style.ERROR(f'  {hash8}.pdf: {msg}'))
            safe_name = _annual_safe_name(pdf_file.name, None, parsed.get('report_year'), hash8)
            if not dry_run:
                self._log_import(safe_name, 0, 'error', msg, content_hash=full_hash)
            return 0, 1, msg, parsed, safe_name, 'error'

        safe_name = _annual_safe_name(pdf_file.name, account.name, parsed.get('report_year'), hash8)
        records_created = 0

        # ── Pass 1: duplicate/conflict per currency section (all-or-nothing) ──
        plans = []          # (currency, cdata, action)
        conflict_msgs = []
        for currency, cdata in [('CAD', parsed.get('cad')), ('USD', parsed.get('usd'))]:
            if cdata is None:
                continue
            existing = RbcAnnual.objects.filter(
                account=account,
                report_year=parsed['report_year'],
                currency=currency,
            )
            if existing.exists():
                stored_hashes = set(existing.values_list('content_hash', flat=True))
                if full_hash in stored_hashes:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  Duplicate annual: {safe_name} '
                            f'(year={parsed["report_year"]}, {currency})'
                        )
                    )
                    plans.append((currency, cdata, 'duplicate'))
                else:
                    # A stored row without content_hash counts as different
                    # content (tdw, Oct 27).
                    msg = (f'Conflict: {account.name} {currency} year='
                           f'{parsed["report_year"]} already stored with '
                           'different content')
                    conflict_msgs.append(msg)
                    self.stderr.write(self.style.ERROR(f'  {msg}'))
            else:
                plans.append((currency, cdata, 'import'))

        # A conflict rejects the whole file: nothing from it is stored.
        if conflict_msgs:
            msg = '; '.join(conflict_msgs)
            self._log_import(safe_name, 0, 'error', msg, content_hash=full_hash)
            return 0, 1, msg, parsed, safe_name, 'conflict'

        # ── Pass 2: store the non-duplicate sections ─────────────────────
        for currency, cdata, action in plans:
            if action == 'duplicate':
                continue

            RbcAnnual.objects.create(
                account=account,
                file_name=safe_name,
                report_year=parsed['report_year'],
                report_period_start=parsed['report_period_start'],
                report_period_end=parsed['report_period_end'],
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
                content_hash=full_hash,
            )
            records_created += 1

        if records_created > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f'  Imported annual: {safe_name} -> {records_created} record(s)'
                )
            )
            self._log_import(safe_name, records_created, 'ok', '',
                             content_hash=full_hash)
            return records_created, 0, '', parsed, safe_name, 'imported'

        # Nothing new, nothing wrong: the whole file is a duplicate and moves
        # to processed/ normally (Q5, tdw Oct 27).
        self.stdout.write(self.style.WARNING(f'  All duplicates: {safe_name}'))
        self._log_import(safe_name, 0, 'duplicate', 'All records already exist',
                         content_hash=full_hash)
        return 0, 0, '', parsed, safe_name, 'duplicate'

    # ------------------------------------------------------------------
    # Monthly processing
    # ------------------------------------------------------------------

    def _process_monthly(
        self, pdf_file: Path, dry_run: bool, name_map: dict[str, str],
        hash8: str, full_hash: str,
    ) -> tuple[int, int, int, int, int, list, list, str | None]:
        """Parse and import a monthly statement PDF.

        Returns (statements_created, transactions_imported, balances_recorded,
                 holdings_recorded, error_count, json_sections, error_messages,
                 safe_name, parser_used, self_check_reasons). ``safe_name`` is
                 None in dry-run mode.
        """
        parsed_list = None
        parser_used = 'text'
        self_check_reasons: list[str] = []

        # Text-first (step 12b): deterministic text parser, then self-check;
        # any reason (or a text-parser error) falls back to the VL parser,
        # whose behaviour is unchanged.
        try:
            text_sections = parse_rbc_pdf_multi(str(pdf_file))
            # The whole text again for the currency-section count check: an
            # unrecognised section header must fail, not pass silently.
            full_text = _extract_text_from_pdf(str(pdf_file))
            self_check_reasons = self_check_sections(
                text_sections, full_text=full_text,
            )
        except Exception as exc:
            logger.warning("text parser failed for %s — falling back to VL: %s", hash8, exc)
            text_sections = None

        if text_sections and not self_check_reasons:
            parsed_list = text_sections
            # Scope (tdw, Oct 21): the text path stores no PDF transactions —
            # holdings, cash and totals only; CSV Activity is the master.
            for sec in parsed_list:
                sec['transactions'] = []
            self.stdout.write(self.style.SUCCESS('  Parser: text (self-check passed)'))
        else:
            parser_used = 'vl'
            if self_check_reasons:
                # Reasons name only fields, currencies and symbols — no values.
                self.stdout.write(
                    self.style.WARNING(
                        '  Self-check failed, falling back to VL: '
                        + '; '.join(self_check_reasons)
                    )
                )
            try:
                parsed_list = parse_rbc_monthly_vl(str(pdf_file))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'  Parse error: {exc}'))
                if not dry_run:
                    safe_name = _unattributable_safe_name(pdf_file.name, hash8)
                    self._log_import(safe_name, 0, 'error', str(exc),
                                     content_hash=full_hash)
                    return (0, 0, 0, 0, 1, [], [str(exc)], safe_name,
                            parser_used, self_check_reasons)
                return (0, 0, 0, 0, 1, [], [str(exc)], None,
                        parser_used, self_check_reasons)

            if parsed_list is None:
                empty_msg = 'Parser returned no sections (possible unsupported statement format)'
                if dry_run:
                    self.stderr.write(self.style.WARNING(f'  [DRY-RUN] {empty_msg}'))
                    return (0, 0, 0, 0, 1, [], [empty_msg], None,
                            parser_used, self_check_reasons)
                safe_name = _unattributable_safe_name(pdf_file.name, hash8)
                return (0, 0, 0, 0, 1, [], [empty_msg], safe_name,
                        parser_used, self_check_reasons)

        # parsed_list is a list of dicts, one per currency section (CAD, USD, etc.)
        account = None
        safe_name = None  # computed once, on the first resolvable section
        stmts_created = 0
        txns_imported = 0
        bals_imported = 0
        holdings_imported = 0
        file_errors = []
        json_sections = []
        plans = []          # per-section: parsed/currency/statement_date/action
        conflict_msgs = []

        # ── Pass 1: validate every section (all-or-nothing, tdw Oct 27) ──
        for parsed in parsed_list:
            statement_date = _coerce_statement_date(parsed.get('statement_date'))
            account_number = parsed.get('account_number', '')
            currency = parsed.get('currency', 'CAD')

            # Always capture parsed data for JSON log
            json_sections.append(parsed)

            if dry_run:
                acct = lookup_account(account_number) if account_number else None
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  [DRY-RUN] Would import monthly [{currency}]: '
                        f'{acct.name if acct else "UNKNOWN_ACCOUNT"} {statement_date} '
                        f'\u2014 {len(parsed.get("transactions", []))} txns, '
                        f'{len(parsed.get("holdings", []))} holdings, '
                        f'{len(parsed.get("balances", []))} balances'
                    )
                )
                stmts_created += 1
                continue

            if statement_date is None:
                # No usable date: the section cannot be stored and must not
                # reach safe_file_name (which would raise and stop the whole
                # run). The file falls back to the unattributable
                # UNKNOWN_ACCOUNT name below when no other section yields one.
                msg = 'Parser did not return statement_date'
                file_errors.append({'currency': currency, 'error': msg})
                self.stderr.write(self.style.ERROR(f'  Skipping [{currency}]: {msg}'))
                continue

            if account is None:
                account = _get_or_create_account(account_number, '')
                if account is None:
                    msg = 'Cannot resolve account for the parsed account number (UNKNOWN_ACCOUNT)'
                    file_errors.append({'currency': currency, 'error': msg})
                    self.stderr.write(self.style.ERROR(f'  Skipping [{currency}]: {msg}'))
                    continue

            if safe_name is None:
                safe_name = safe_file_name(
                    pdf_file.name, account.name, MONTHLY,
                    date=statement_date, hash8=hash8,
                )

            # Duplicate check by content (step 13b): the file name no longer
            # takes part in the key. A stored row with this file's
            # content_hash → duplicate section; any other hit — including a
            # row without content_hash — → conflict (tdw, Oct 27).
            existing = RbcMonthly.objects.filter(
                account=account,
                statement_date=statement_date,
                currency=currency,
            )
            if existing.exists():
                stored_hashes = set(existing.values_list('content_hash', flat=True))
                if full_hash in stored_hashes:
                    plans.append({'parsed': parsed, 'currency': currency,
                                  'statement_date': statement_date,
                                  'action': 'duplicate'})
                else:
                    msg = (f'Conflict: {account.name} {currency} {statement_date} '
                           'already stored with different content')
                    conflict_msgs.append(msg)
                    self.stderr.write(self.style.ERROR(f'  [{currency}] {msg}'))
                continue

            plans.append({'parsed': parsed, 'currency': currency,
                          'statement_date': statement_date,
                          'action': 'import'})

        if dry_run:
            return (stmts_created, 0, 0, 0, 0, [], [], None,
                    parser_used, self_check_reasons, 'imported')

        # A conflict rejects the whole file: nothing from it is stored.
        if conflict_msgs:
            safe_name = safe_name or _unattributable_safe_name(pdf_file.name, hash8)
            msg = '; '.join(conflict_msgs)
            self._log_import(safe_name, 0, 'error', msg, content_hash=full_hash)
            return (0, 0, 0, 0, len(file_errors) + len(conflict_msgs),
                    json_sections,
                    [e['error'] for e in file_errors] + conflict_msgs,
                    safe_name, parser_used, self_check_reasons, 'conflict')

        # ── Pass 2: store the non-duplicate sections ─────────────────────
        for plan in plans:
            parsed = plan['parsed']
            currency = plan['currency']
            statement_date = plan['statement_date']

            if plan['action'] == 'duplicate':
                self.stdout.write(
                    self.style.WARNING(
                        f'  Duplicate monthly [{currency}]: {safe_name} '
                        f'(account={account}, date={statement_date})'
                    )
                )
                continue

            rbc_monthly = RbcMonthly.objects.create(
                account=account,
                file_name=safe_name,
                statement_date=statement_date,
                currency=currency,
                last_statement_date=parsed.get('last_statement_date'),
                content_hash=full_hash,
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
                    security=h.get('security', ''),
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
                    description=scrub_text(tx['description'] or '', name_map)[0][:500],
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
                    f'  Imported monthly [{currency}]: {account.name} {statement_date} '
                    f'\u2014 {len(txs_to_create)} txns, '
                    f'{len(holdings_to_create)} holdings, '
                    f'{len(balances_to_create)} balances'
                )
            )

        if dry_run:
            return (stmts_created, 0, 0, 0, 0, [], [], None,
                    parser_used, self_check_reasons)

        # No section could be attributed to an account (or had a date).
        if safe_name is None:
            safe_name = _unattributable_safe_name(pdf_file.name, hash8)

        if stmts_created > 0:
            verdict = 'imported'
            self._log_import(
                safe_name,
                stmts_created + txns_imported + bals_imported + holdings_imported,
                'success',
                '',
                content_hash=full_hash,
            )
        elif plans and all(p['action'] == 'duplicate' for p in plans):
            # Nothing new, nothing wrong: the whole file is a duplicate and
            # moves to processed/ normally (Q5, tdw Oct 27).
            verdict = 'duplicate'
            self._log_import(safe_name, 0, 'duplicate',
                             'All sections already imported',
                             content_hash=full_hash)
        else:
            verdict = 'error'

        return (stmts_created, txns_imported, bals_imported, holdings_imported,
                len(file_errors), json_sections, [e['error'] for e in file_errors],
                safe_name, parser_used, self_check_reasons, verdict)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_import(self, filename: str, records: int, status: str, error_msg: str,
                    content_hash: str = ""):
        """Create a FileImportLog entry."""
        FileImportLog.objects.create(
            filename=filename,
            broker='RBC',
            imported_at=timezone.now(),
            records_imported=records,
            records_skipped=0,
            status=status,
            error_message=error_msg,
            content_hash=content_hash,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _normalize_rbc_account_id(raw: str) -> str:
    """Extract the 8-digit canonical RBC account ID from any format.

    Delegates to the shared normaliser (core/utils/account_fingerprint.py):
    '900-00003-1-8' -> '90000003', '9000000318' -> '90000003',
    '90000003' -> '90000003'.
    """
    return normalize_account_number(raw)


def _get_or_create_account(account_number: str, account_type_code: str) -> Account | None:
    """Look up an existing Account by fingerprint only.

    Never auto-creates: returns ``None`` when the number has no stored
    fingerprint (the caller rejects the file via its error path).
    """
    if not account_number:
        return None
    return lookup_account(account_number)


def _annual_safe_name(original_name, account_name, report_year, hash8: str) -> str:
    """Safe name for an annual PDF; falls back to the unknown pattern when the
    account or the report year is missing."""
    year = str(report_year or '').strip()
    if account_name and len(year) == 4 and year.isdigit():
        return safe_file_name(original_name, account_name, ANNUAL, date=year, hash8=hash8)
    return _unattributable_safe_name(original_name, hash8)


def _unattributable_safe_name(original_name: str, hash8: str) -> str:
    """Safe name for a PDF that cannot be attributed to an account."""
    return safe_file_name(original_name, 'UNKNOWN_ACCOUNT', UNKNOWN, hash8=hash8)


def _unique_destination(directory: Path, name: str) -> Path:
    """``directory/name``, uniquified with a ``_dup`` suffix if it exists.

    Re-imported duplicate content must never overwrite the file (or its JSON
    log) from the original import.
    """
    dest = directory / name
    if not dest.exists():
        return dest
    stem, dot, ext = name.rpartition('.')
    base = stem if dot else name
    suffix = f'.{ext}' if dot else ''
    n = 1
    while True:
        candidate = directory / f'{base}_dup{n}{suffix}'
        if not candidate.exists():
            return candidate
        n += 1


def _coerce_statement_date(value):
    """Return ``value`` as a ``datetime.date``, or ``None`` when it is not a
    usable YYYY-MM-DD statement date (missing, empty or malformed).

    A section with no usable date must be treated as unattributable —
    passing the raw value to :func:`safe_file_name` would raise ``ValueError``
    and stop the whole import run.
    """
    if isinstance(value, date_type):
        return value
    text = str(value or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return None
    try:
        return date_type.fromisoformat(text)  # rejects e.g. 2025-13-40
    except ValueError:
        return None


def _json_log_path(json_dir: Path, safe_name: str, has_error: bool) -> Path:
    """JSON log path derived from the safe name (``_ERROR`` suffix on errors)."""
    stem = safe_name.rsplit('.', 1)[0]
    return json_dir / f'{stem}{"_ERROR" if has_error else ""}.json'


def _parsed_account_sections(data):
    """Yield every dict in a JSON log payload that carries an ``account_number``."""
    for section in data.get('sections') or []:
        if isinstance(section, dict):
            yield section
    annual = data.get('data')
    if isinstance(annual, dict):
        yield annual


def _scrubbed_json(data, name_map: dict[str, str]) -> str:
    """Serialise and scrub a JSON log payload before it is written.

    The VL payload carries the parsed ``account_number``; ``scrub_text`` alone
    would leave it when the number has no stored fingerprint (unmatched
    candidates are untouched by design). So, before dumping: every parsed
    account number is labelled — the account name when its fingerprint is in
    ``name_map``, else ``UNKNOWN_ACCOUNT`` — the field itself is set to that
    label, and every other occurrence of the same value (as parsed and in
    normalized form) is replaced with it, so a number repeated in a
    description or raw text field cannot survive (tdw decision, Oct 25).
    The dump then goes through ``scrub_text`` for fingerprint matches and
    15-digit bank runs; there is no general digit scrub. Raises (loudly) if
    the fingerprint key is missing — never writes an unscrubbed payload.
    """
    labels = {}
    for section in _parsed_account_sections(data):
        raw = section.get('account_number')
        if not raw or raw in labels:
            continue
        label = name_map.get(fingerprint_account_number(str(raw)), 'UNKNOWN_ACCOUNT')
        labels[raw] = label
        section['account_number'] = label

    payload = json.dumps(data, indent=2, default=str)
    for raw, label in labels.items():
        for value in dict.fromkeys((str(raw), normalize_account_number(str(raw)))):
            if len(value) >= 8:  # only plausible account numbers, never short tokens
                payload = payload.replace(value, label)
    return scrub_text(payload, name_map)[0]


def _move_file(src: Path, dest: Path, label: str) -> bool:
    """Move src to dest, retrying up to 5 times on PermissionError.

    ``label`` is the content hash8 used in log lines — the original file name
    carries the account number and must never be logged. Returns True if the
    file was moved, False otherwise. If the source file is already gone (moved
    by a concurrent process), logs a warning and returns False without raising.
    """
    if not src.exists():
        logger.warning("_move_file: %s not found — already moved by another process", label)
        return False
    for attempt in range(5):
        try:
            shutil.move(str(src), str(dest))
            logger.info("Moved %s -> %s", label, dest.name)
            return True
        except PermissionError:
            if attempt == 4:
                logger.error("Failed to move %s after 5 attempts", label)
                return False
            delay = 2 * (attempt + 1)
            logger.warning("File locked, retrying move in %ds...", delay)
            time.sleep(delay)
        except FileNotFoundError:
            logger.warning("_move_file: %s disappeared during move — already moved by another process", label)
            return False
        except Exception as exc:
            logger.error("Cannot move %s: %s", label, exc)
            return False
    return False

