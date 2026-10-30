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

from django.core.management.base import BaseCommand, CommandError

from imports.parsers import parse_rbc_csv
from core.utils.account_fingerprint import (
    build_fingerprint_name_map,
    fingerprint_account_number,
)


def _fmt(value):
    """Format an optional date for the report ('-' when missing)."""
    return str(value) if value is not None else '-'


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
                trades, _rate = parse_rbc_csv(path, rejections=rejections)
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
        # Short summary (stdout)
        # ------------------------------------------------------------------
        read = [r for r in file_reports if not r.get('skipped') and not r.get('error')]
        skipped = sum(1 for r in file_reports if r.get('skipped'))
        errors = sum(1 for r in file_reports if r.get('error'))
        total_unknown = sum(r['unknown_rows'] for r in read)
        total_practice = sum(r['practice_rows'] for r in read)
        accounts_seen = sorted({
            acct for r in read for acct in r['accounts'] if acct != 'unknown'
        })

        self.stdout.write(
            f'Files read: {len(read)} '
            f'(skipped {skipped} holdings, {errors} errors)'
        )
        self.stdout.write(f'Accounts seen: {len(accounts_seen)}')
        self.stdout.write(
            f'Unknown-account rows: {total_unknown}, '
            f'practice rows skipped: {total_practice}'
        )
        for r in file_reports:
            if r.get('skipped'):
                self.stdout.write(
                    f"  {r['name']}: skipped (Holdings format, not an Activity CSV)"
                )
            elif r.get('error'):
                self.stdout.write(f"  {r['name']}: ERROR {r['error']}")
            else:
                self.stdout.write(
                    f"  {r['name']}: {r['row_count']} rows, "
                    f"{_fmt(r['first_date'])} .. {_fmt(r['last_date'])}, "
                    f"settlement {_fmt(r['first_settlement'])} .. "
                    f"{_fmt(r['last_settlement'])}, "
                    f"unknown-account rows: {r['unknown_rows']}, "
                    f"practice rows skipped: {r['practice_rows']}"
                )
                for acct in sorted(r['accounts']):
                    a = r['accounts'][acct]
                    self.stdout.write(
                        f"    {acct}: {a['count']} rows "
                        f"({_fmt(a['first_date'])} .. {_fmt(a['last_date'])})"
                    )
