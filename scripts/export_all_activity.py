"""Export every activity line from all RBC activity CSVs into a single master CSV.

No filtering, no dedup, no parsing — raw row dump with source filename.

Usage:
    python scripts/export_all_activity.py
"""

import csv
import glob
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_INBOX = 'inbox'
DEFAULT_OUTPUT = Path('docs', 'reconciliation', 'CSV_Activity_detail.csv')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_activity_csvs(inbox: str) -> List[str]:
    """Find all CSV files in the directory (including subdirectories).

    No filtering - export everything for comparison.
    """
    csv_files = []
    for root, dirs, files in os.walk(inbox):
        for fname in files:
            if fname.lower().endswith('.csv'):
                csv_files.append(os.path.join(root, fname))
    return sorted(csv_files)


def _parse_rbc_csv(file_path: str) -> List[dict]:
    """Parse an RBC Activity CSV and return a list of raw row dicts.

    Only exports the actual activity data lines (rows with a populated Date).
    Metadata lines ("Account:", "Trades this month:", etc.) are skipped.
    """
    rows = []
    col_map = {}  # rebuilt after header detected
    date_idx = None

    found_header = False

    try:
        with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.reader(csvfile)

            for row in reader:
                if not row or all(cell.strip() == '' for cell in row):
                    continue

                # Skip exchange rate line
                if 'Exchange Rate:' in ' '.join(row):
                    continue

                # Detect header row and build dynamic column mapping
                if not found_header:
                    if row[0].strip('"') == 'Date':
                        found_header = True
                        header = [cell.strip().strip('"').lower() for cell in row]
                        for i, col_name in enumerate(header):
                            if 'date' in col_name and 'settlement' not in col_name:
                                col_map['date'] = i
                                date_idx = i
                            elif 'activity' in col_name:
                                col_map['activity'] = i
                            elif 'symbol' in col_name and 'description' not in col_name:
                                col_map['symbol'] = i
                            elif 'quantity' in col_name:
                                col_map['quantity'] = i
                            elif 'price' in col_name:
                                col_map['price'] = i
                            elif 'settlement' in col_name:
                                col_map['settlement'] = i
                            elif 'account' in col_name:
                                col_map['account'] = i
                            elif 'value' in col_name:
                                col_map['value'] = i
                            elif 'currency' in col_name:
                                col_map['currency'] = i
                            elif 'description' in col_name:
                                col_map['description'] = i
                            elif 'notes' in col_name:
                                col_map['notes'] = i
                        continue

                # Skip rows before header (metadata, totals, disclaimers)
                if not found_header:
                    continue

                # Skip metadata lines — real dates contain a comma ("Dec 02, 2021")
                if date_idx is not None and date_idx < len(row):
                    date_str = row[date_idx].strip().strip('"')
                    if ',' not in date_str:
                        continue
                else:
                    continue

                row_dict = {}
                for key, idx in col_map.items():
                    if idx is not None and idx < len(row):
                        row_dict[key] = row[idx].strip().strip('"')
                    else:
                        row_dict[key] = ''

                rows.append(row_dict)

    except (FileNotFoundError, PermissionError) as e:
        logger.error("Error reading %s: %s", file_path, e)

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(inbox: str = DEFAULT_INBOX, output: Path = DEFAULT_OUTPUT) -> None:
    """Export every activity line from all CSVs into a single master file."""
    inbox_path = Path(inbox)
    if not inbox_path.is_dir():
        print(f"Error: Inbox directory not found: {inbox_path}", file=sys.stderr)
        sys.exit(1)

    csv_files = _find_activity_csvs(str(inbox_path))
    logger.info("Found %d activity CSV files in %s", len(csv_files), inbox_path)

    # Collect all rows
    all_rows = []
    file_counts = {}
    total_value = Decimal('0.00')

    for file_path in csv_files:
        filename = os.path.basename(file_path)
        rows = _parse_rbc_csv(file_path)
        file_counts[filename] = len(rows)
        for row in rows:
            val_str = row.get('value', '0').strip()
            if val_str:
                try:
                    total_value += Decimal(val_str.replace(',', '').strip())
                except Exception:
                    pass
            all_rows.append((filename, row))

    # Write master CSV
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ['Source_File'] + list(_find_column_keys())
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for filename, row in all_rows:
            out_row = {'Source_File': filename}
            for key in fieldnames[1:]:
                out_row[key] = row.get(key, '')
            writer.writerow(out_row)

    # Print summary
    print(f"\n{'=' * 80}")
    print(f"CSV Activity Detail Export")
    print(f"{'=' * 80}")
    print(f"Inbox: {inbox_path}")
    print(f"Output: {output_path}")
    print(f"Total files: {len(csv_files)}")
    print(f"Total activity lines: {len(all_rows)}")
    print(f"Value sum: {total_value:,.2f}")
    print(f"")
    print(f"{'Filename':<55} {'Activity_Count':>15}")
    print(f"{'-' * 55} {'-' * 15}")
    for filename, count in file_counts.items():
        print(f'{filename:<55} {count:>15}')
    print(f"{'=' * 80}\n")


def _find_column_keys():
    """Return sorted list of all column names from the RBC CSV column map."""
    col_map = {
        'date': 0,
        'activity': 1,
        'symbol': 2,
        'quantity': 3,
        'price': 4,
        'settlement': 5,
        'account': 6,
        'value': 7,
        'currency': 8,
        'description': 9,
        'notes': 10,
    }
    # Also include common extra columns that might appear
    extra_cols = [
        'exchange_rate', 'exchange_rate_from', 'exchange_rate_to',
        'exchange_rate_date', 'exchange_rate_time', 'exchange_rate_source',
        'description', 'notes',
    ]
    return sorted(set(list(col_map.keys()) + extra_cols))


import argparse


def parse_args():
    parser = argparse.ArgumentParser(description='Export all activity lines to a master CSV')
    parser.add_argument('--inbox', default=DEFAULT_INBOX, help='Path to CSV files directory (default: inbox)')
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT), help='Path to output CSV file')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    main(args.inbox, Path(args.output))
