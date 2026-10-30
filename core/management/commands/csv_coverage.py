"""Read-only coverage audit of a folder of RBC Activity CSV files.

Audits every ``*.csv`` in the folder given by ``--dir`` using the same CSV
reading and fingerprint account lookup as ``import_csv`` (``parse_rbc_csv``),
and writes a coverage report: per-file stats, per-account breakdown, overlaps
between file pairs, gaps of more than 31 days, and a monthly CSV-vs-PDF table
from 2017-11.

Privacy: the report contains only account names, file names, dates and counts.
Never any value from the CSV ``Account`` column — not even the last 4 digits.
Unknown accounts are reported only as a count of "unknown-account rows".

Plan: docs/plans/csv-coverage.md. This command writes nothing to the database.
"""

import os
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from core.models import RbcMonthlyTransaction
from imports.parsers import parse_rbc_csv
from core.utils.account_fingerprint import (
    build_fingerprint_name_map,
    fingerprint_account_number,
)


def _fmt(value):
    """Format an optional date for the report ('-' when missing)."""
    return str(value) if value is not None else '-'


MONTH_START = (2017, 11)


def _month_range(start, end):
    """Inclusive range of (year, month) tuples from start to end."""
    y, m = start
    while (y, m) <= end:
        yield (y, m)
        m += 1
        if m == 13:
            y, m = y + 1, 1


def row_identity(trade: dict) -> tuple:
    """Identity of a parsed row for overlap/dedup (plan, decision 4).

    All parsed fields except ``row_number`` — date, settlement_date, symbol,
    type, quantity, price, commission, amount, currency, notes — plus the
    row's account id. The account id is used internally only and must never
    be printed or written; without it, equal rows in two different accounts
    would count as one. Values are compared, never displayed.
    """
    return (
        trade['account_id'],
        trade['date'],
        trade.get('settlement_date'),
        trade['symbol'],
        trade['type'],
        trade['quantity'],
        trade['price'],
        trade['commission'],
        trade['amount'],
        trade['currency'],
        trade['notes'],
    )


class Command(BaseCommand):
    help = (
        "Read-only coverage audit of a folder of RBC Activity CSV files: "
        "per-file stats, per-account breakdown, overlaps between file pairs, "
        "gaps > 31 days and a monthly CSV-vs-PDF table from 2017-11. "
        "Writes nothing to the database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dir', dest='csv_dir', required=True,
            help='Folder of RBC Activity CSV files (non-recursive *.csv)',
        )
        parser.add_argument(
            '--out', default=None,
            help='Optional path for the full report file (short summary always goes to stdout)',
        )

    def handle(self, *args, **options):
        csv_dir = options['csv_dir']
        out_path = options['out']

        if not os.path.isdir(csv_dir):
            raise CommandError(f'--dir is not a directory: {csv_dir}')

        file_names = sorted(
            name for name in os.listdir(csv_dir)
            if name.lower().endswith('.csv')
            and os.path.isfile(os.path.join(csv_dir, name))
        )

        # ------------------------------------------------------------------
        # Per-file stats (parse errors reported per file; continue with rest)
        # ------------------------------------------------------------------
        # fingerprint -> account name, built once (names only, never numbers).
        name_map = build_fingerprint_name_map()

        file_reports = []
        trades_by_file = {}
        for name in file_names:
            path = os.path.join(csv_dir, name)
            if name.lower().startswith('holdings'):
                # Holdings exports are a different format — skip with a note.
                file_reports.append({'name': name, 'skipped': True})
                continue

            rejections = []
            try:
                trades, _rate, _accounting = parse_rbc_csv(path, rejections=rejections)
            except Exception as exc:  # non-Activity CSV or unreadable file
                file_reports.append({'name': name, 'error': str(exc)})
                continue

            dates = sorted(t['date'] for t in trades)
            settlements = sorted(
                t['settlement_date'] for t in trades
                if t.get('settlement_date') is not None
            )

            # Rejections: unknown-account rows vs practice rows (PRAC…). The
            # rejection reason embeds the raw account number — count only,
            # never echo it.
            unknown_rows = sum(
                1 for rj in rejections
                if rj['Reason Rejected'].startswith('Unknown account')
            )
            practice_rows = sum(
                1 for rj in rejections
                if rj['Reason Rejected'].startswith('Practice account')
            )

            # Per-account breakdown (by name; the raw account id is used only
            # as a fingerprint lookup key and never printed or written).
            accounts = {}
            for t in trades:
                acct_name = name_map.get(
                    fingerprint_account_number(t['account_id']), 'unknown'
                )
                entry = accounts.setdefault(
                    acct_name, {'count': 0, 'first_date': None, 'last_date': None}
                )
                entry['count'] += 1
                if entry['first_date'] is None or t['date'] < entry['first_date']:
                    entry['first_date'] = t['date']
                if entry['last_date'] is None or t['date'] > entry['last_date']:
                    entry['last_date'] = t['date']

            file_reports.append({
                'name': name,
                'row_count': len(trades),
                'first_date': dates[0] if dates else None,
                'last_date': dates[-1] if dates else None,
                'first_settlement': settlements[0] if settlements else None,
                'last_settlement': settlements[-1] if settlements else None,
                'unknown_rows': unknown_rows,
                'practice_rows': practice_rows,
                'accounts': accounts,
            })
            trades_by_file[name] = trades

        # ------------------------------------------------------------------
        # Per-account analysis: group rows by resolved account (fingerprint).
        # The fingerprint is a lookup key only; the report shows names.
        # ------------------------------------------------------------------
        account_data = {}
        for fname, trades in trades_by_file.items():
            for t in trades:
                fp = fingerprint_account_number(t['account_id'])
                acct_name = name_map.get(fp)
                if acct_name is None:
                    continue  # unresolvable — counted per file, not analysed
                acct = account_data.setdefault(
                    fp, {'name': acct_name, 'files': {}}
                )
                acct['files'].setdefault(fname, []).append(t)

        # Overlaps between file pairs (same account): overlapping Date range
        # and how many rows inside it are identical in both files.
        overlap_lines = []
        for fp in sorted(account_data, key=lambda f: account_data[f]['name']):
            acct = account_data[fp]
            fnames = sorted(acct['files'])
            pairs = []
            for i in range(len(fnames)):
                for j in range(i + 1, len(fnames)):
                    fa, fb = fnames[i], fnames[j]
                    ta, tb = acct['files'][fa], acct['files'][fb]
                    start = max(min(t['date'] for t in ta),
                                min(t['date'] for t in tb))
                    end = min(max(t['date'] for t in ta),
                              max(t['date'] for t in tb))
                    if start > end:
                        continue
                    ca = Counter(row_identity(t) for t in ta
                                 if start <= t['date'] <= end)
                    cb = Counter(row_identity(t) for t in tb
                                 if start <= t['date'] <= end)
                    identical = sum(min(ca[k], cb[k])
                                    for k in set(ca) & set(cb))
                    pairs.append(
                        f'    {fa} x {fb}: {_fmt(start)} .. {_fmt(end)}, '
                        f'{identical} identical rows'
                    )
            overlap_lines.append((acct['name'], pairs))

        # Gaps of more than 31 days between consecutive row dates per account,
        # naming the file(s) on each side of the gap.
        gap_lines = []
        total_gaps = 0
        for fp in sorted(account_data, key=lambda f: account_data[f]['name']):
            acct = account_data[fp]
            date_files = {}
            for fname, trades in acct['files'].items():
                for t in trades:
                    date_files.setdefault(t['date'], set()).add(fname)
            dates = sorted(date_files)
            gaps = []
            for a, b in zip(dates, dates[1:]):
                gap_days = (b - a).days
                if gap_days > 31:
                    gaps.append(
                        f'    {_fmt(a)} ({", ".join(sorted(date_files[a]))}) -> '
                        f'{_fmt(b)} ({", ".join(sorted(date_files[b]))}): '
                        f'{gap_days} days'
                    )
            total_gaps += len(gaps)
            gap_lines.append((acct['name'], gaps))

        # Monthly CSV-vs-PDF table per account, from 2017-11 to the latest
        # month seen in the CSVs or in that account's RbcMonthlyTransaction
        # rows. CSV count uses max-multiplicity dedup: a row identical across
        # files counts once; duplicates within one file are kept.
        monthly_lines = []
        total_flagged = 0
        for fp in sorted(account_data, key=lambda f: account_data[f]['name']):
            acct = account_data[fp]
            per_file = [
                Counter(row_identity(t) for t in trades)
                for trades in (acct['files'][f] for f in sorted(acct['files']))
            ]
            all_ids = set()
            for c in per_file:
                all_ids |= set(c)
            csv_months = Counter()
            for ident in all_ids:
                m = max(c[ident] for c in per_file)
                csv_months[(ident[1].year, ident[1].month)] += m

            pdf_dates = list(RbcMonthlyTransaction.objects.filter(
                rbc_monthly__account__fingerprint=fp,
            ).values_list('date', flat=True))
            pdf_months = Counter((d.year, d.month) for d in pdf_dates)

            end_candidates = set(csv_months) | set(pdf_months)
            if not end_candidates or max(end_candidates) < MONTH_START:
                monthly_lines.append((acct['name'], []))
                continue

            lines = []
            flagged = 0
            for ym in _month_range(MONTH_START, max(end_candidates)):
                c = csv_months.get(ym, 0)
                p = pdf_months.get(ym, 0)
                flag = ''
                if p > 0 and c == 0:
                    flag = '  FLAGGED (PDF rows, no CSV rows)'
                    flagged += 1
                lines.append(f'    {ym[0]:04d}-{ym[1]:02d}: csv={c} pdf={p}{flag}')
            total_flagged += flagged
            monthly_lines.append((acct['name'], lines))

        # ------------------------------------------------------------------
        # Report + output. Full report to --out when given; the short summary
        # always goes to stdout. Privacy: only account names, file names,
        # dates and counts — never any value from the CSV Account column
        # (raw ids appear above only as fingerprint lookup keys).
        # ------------------------------------------------------------------
        read = [r for r in file_reports if not r.get('skipped') and not r.get('error')]
        skipped = sum(1 for r in file_reports if r.get('skipped'))
        errors = sum(1 for r in file_reports if r.get('error'))
        total_unknown = sum(r['unknown_rows'] for r in read)
        total_practice = sum(r['practice_rows'] for r in read)
        accounts_seen = sorted({
            acct for r in read for acct in r['accounts'] if acct != 'unknown'
        })

        report_lines = [
            'CSV coverage report',
            f'Dir: {csv_dir}',
            '',
            'Files:',
        ]
        for r in file_reports:
            if r.get('skipped'):
                report_lines.append(
                    f"  {r['name']}: skipped (Holdings format, not an Activity CSV)"
                )
            elif r.get('error'):
                report_lines.append(f"  {r['name']}: ERROR {r['error']}")
            else:
                report_lines.append(
                    f"  {r['name']}: {r['row_count']} rows, "
                    f"{_fmt(r['first_date'])} .. {_fmt(r['last_date'])}, "
                    f"settlement {_fmt(r['first_settlement'])} .. "
                    f"{_fmt(r['last_settlement'])}, "
                    f"unknown-account rows: {r['unknown_rows']}, "
                    f"practice rows skipped: {r['practice_rows']}"
                )
                for acct in sorted(r['accounts']):
                    a = r['accounts'][acct]
                    report_lines.append(
                        f"    {acct}: {a['count']} rows "
                        f"({_fmt(a['first_date'])} .. {_fmt(a['last_date'])})"
                    )

        gaps_by_name = dict(gap_lines)
        monthly_by_name = dict(monthly_lines)
        for acct_name, pairs in overlap_lines:
            report_lines.append('')
            report_lines.append(f'Account: {acct_name}')
            if pairs:
                report_lines.append('  overlaps:')
                report_lines.extend(pairs)
            else:
                report_lines.append('  overlaps: none')
            gaps = gaps_by_name.get(acct_name, [])
            if gaps:
                report_lines.append('  gaps > 31 days:')
                report_lines.extend(gaps)
            mlines = monthly_by_name.get(acct_name, [])
            if mlines:
                report_lines.append('  monthly (from 2017-11):')
                report_lines.extend(mlines)

        if out_path:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(report_lines) + '\n')
            self.stdout.write(f'Report written to {out_path}')

        # Short summary (always stdout).
        self.stdout.write(
            f'Files read: {len(read)} '
            f'(skipped {skipped} holdings, {errors} errors)'
        )
        self.stdout.write(f'Accounts seen: {len(accounts_seen)}')
        self.stdout.write(
            f'Unknown-account rows: {total_unknown}, '
            f'practice rows skipped: {total_practice}'
        )
        self.stdout.write(f'Gaps found: {total_gaps}')
        self.stdout.write(f'Flagged months: {total_flagged}')
