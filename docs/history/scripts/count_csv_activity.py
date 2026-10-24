"""Count activity lines and sum Value column in all RBC activity CSVs.

Scans the inbox directory for Activity CSV files, counts the number of
data rows (activity lines), and sums the 'Value' column in each file.

Outputs a CSV report to docs/reconciliation/CSV_Prediction_Counts.csv
for comparison with the actual Transaction table in the database.

Usage:
    python scripts/count_csv_activity.py
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
DEFAULT_OUTPUT = Path('docs', 'reconciliation', 'CSV_Prediction_Counts.csv')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_activity_csvs(inbox: str) -> List[str]:
    """Find all Activity CSV files in the inbox directory.

    Excludes Holdings and Dividends files.
    """
    csv_files = []
    for pattern in ['*.csv', '**/*.csv']:
        csv_files.extend(glob.glob(os.path.join(inbox, pattern), recursive=True))
    csv_files = sorted(set(csv_files))

    # Filter out Holdings and Dividends files
    csv_files = [
        f for f in csv_files
        if not (
            os.path.basename(f).lower().startswith('holdings') or
            'dividend' in os.path.basename(f).lower()
        )
    ]
    return csv_files


def _parse_rbc_csv(file_path: str) -> Tuple[int, Decimal]:
    """Parse an RBC Activity CSV and return (activity_count, value_sum).

    Args:
        file_path: Path to the CSV file.

    Returns:
        Tuple of (activity_count, value_sum).
        activity_count is the number of data rows (excluding headers/metadata).
        value_sum is the sum of the Value column (Decimal).
    """
    activity_count = 0
    value_sum = Decimal('0.00')

    col_map = {
        'date': 0,
        'activity': 1,
        'symbol': 2,
        'quantity': 4,
        'price': 5,
        'settlement': 6,
        'account': 7,
        'value': 8,
        'currency': 9,
        'notes': 10,
    }

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

                # Detect header row
                if not found_header:
                    if row[0].strip().strip('"') == 'Date':
                        found_header = True
                        # Build dynamic column mapping
                        header = [cell.strip().strip('"').lower() for cell in row]
                        col_map = {}
                        for i, col_name in enumerate(header):
                            if 'date' in col_name and 'settlement' not in col_name:
                                col_map['date'] = i
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
                            elif 'notes' in col_name:
                                col_map['notes'] = i
                        continue

                # Data row
                if len(row) < 9:
                    continue

                activity_count += 1

                # Sum Value column
                value_str = row[col_map.get('value', 8)].strip().strip('"')
                if value_str:
                    try:
                        value_sum += Decimal(value_str.replace(',', '').strip())
                    except Exception:
                        pass

    except (FileNotFoundError, PermissionError) as e:
        logger.error("Error reading %s: %s", file_path, e)

    return activity_count, value_sum


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(inbox: str = DEFAULT_INBOX, output: Path = DEFAULT_OUTPUT) -> None:
    """Main entry point.

    Scans the inbox directory for Activity CSVs, counts lines and sums values,
    then writes a report CSV.

    Args:
        inbox: Path to the inbox directory.
        output: Path to the output CSV file.
    """
    inbox_path = Path(inbox)
    if not inbox_path.is_dir():
        print(f"Error: Inbox directory not found: {inbox_path}", file=sys.stderr)
        sys.exit(1)

    csv_files = _find_activity_csvs(str(inbox_path))
    logger.info("Found %d activity CSV files in %s", len(csv_files), inbox_path)

    # Parse all CSVs
    results = []
    total_activity = 0
    total_value = Decimal('0.00')

    for file_path in csv_files:
        filename = os.path.basename(file_path)
        logger.info("Processing: %s", filename)

        activity_count, value_sum = _parse_rbc_csv(file_path)
        results.append({
            'Filename': filename,
            'Activity_Count': activity_count,
            'Value_Sum': value_sum,
        })

        total_activity += activity_count
        total_value += value_sum

    # Sort by filename for consistent output
    results.sort(key=lambda x: x['Filename'])

    # Write output CSV
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['Filename', 'Activity_Count', 'Value_Sum'])
        writer.writeheader()
        for result in results:
            writer.writerow(result)

    # Print summary
    print(f"\n{'=' * 80}")
    print(f"CSV Activity Report")
    print(f"{'=' * 80}")
    print(f"Inbox directory: {inbox_path}")
    print(f"Output file: {output_path}")
    print(f"")
    print(f"Total CSV files: {len(csv_files)}")
    print(f"Total activity lines: {total_activity}")
    print(f"Total Value sum: {total_value:,.2f}")
    print(f"")
    print(f"{'Filename':<50} {'Activity_Count':>15} {'Value_Sum':>15}")
    print(f"{'-' * 50} {'-' * 15} {'-' * 15}")
    for result in results:
        print(
            f"{result['Filename']:<50} "
            f"{result['Activity_Count']:>15} "
            f"{result['Value_Sum']:>15,.2f}"
        )
    print(f"{'=' * 80}")
    print(f"Report saved to: {output_path}")
    print(f"{'=' * 80}\n")


if __name__ == '__main__':
    main()
