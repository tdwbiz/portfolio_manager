"""Parser for RBC Holdings CSV exports.

Converts Holdings CSV snapshots into PositionHistory records.
"""

import csv
import io
import logging
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from core.models import Account, PositionHistory

logger = logging.getLogger(__name__)


def parse_holdings_csv(file_path: str) -> list[dict]:
    """Parse an RBC Holdings CSV export.

    Returns a list of dicts, one per row, containing:
        account_id, symbol, quantity, cost_basis_per_unit,
        total_cost_basis, market_price, market_value, currency,
        snapshot_date (extracted from file header).

    Args:
        file_path: Path to the Holdings CSV file.

    Returns:
        List of position dicts.
    """
    positions = []
    snapshot_date = None
    found_header = False
    account_id = None
    filename = os.path.basename(file_path)

    # Try to read the file with various encodings
    raw_content = None
    for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            with open(file_path, 'r', encoding=enc) as f:
                raw_content = f.read()
            break
        except UnicodeDecodeError:
            continue

    if raw_content is None:
        raise UnicodeDecodeError(
            'utf-8', b'', 0, 1, 'Could not decode file with any supported encoding'
        )

    reader = csv.reader(io.StringIO(raw_content))

    # --- Extract account_id (3 sources, priority order) ---

    # 1) Filename: "Holdings 90000004 January 1, 2026.csv"
    if not account_id:
        acct_match = re.search(r'\d{5,}', filename)
        if acct_match:
            account_id = acct_match.group(0)

    # 2) PDF text / header line: "Account: 90000008 - LIF_SUSAN"
    #    Pattern B files have NO Account: line in CSV header section.
    #    But the PDF content has: "Account: 90000008 - LIF_SUSAN"
    if not account_id and raw_content:
        acct_match = re.search(r'Account:\s*(\d{5,})', raw_content)
        if acct_match:
            account_id = acct_match.group(1)

    # 3) First column of first data row (Pattern B fallback):
    #    If no Account: line exists anywhere in the file (very rare),
    #    the first column of the first real data row IS the account ID.
    if not account_id and raw_content:
        # Find the first data row by scanning raw text for header markers
        lines = raw_content.split('\n')
        data_start_idx = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped and re.match(r'\s*\d', stripped):
                data_start_idx = idx
                break

        if data_start_idx is not None:
            first_data_line = lines[data_start_idx].strip()
            acct_match = re.match(r'\d{5,}', first_data_line)
            if acct_match:
                account_id = acct_match.group(0)

    # --- Now parse the CSV data rows ---

    for row_num, row in enumerate(reader, start=1):
        if not row or all(cell.strip() == '' for cell in row):
            continue

        # Extract snapshot date from header
        joined = ' '.join(row).replace(',', ' ')
        joined = re.sub(r'\s+', ' ', joined)
        date_match = re.search(
            r'([A-Za-z]+ \d{1,2},? \d{4})',
            joined,
        )
        if date_match:
            try:
                cleaned = date_match.group(1).replace(',', '').strip()
                # Try abbreviated (Jan) and full (January) month names
                try:
                    snapshot_date = datetime.strptime(cleaned, '%b %d %Y').date()
                except ValueError:
                    snapshot_date = datetime.strptime(cleaned, '%B %d %Y').date()
            except Exception:
                pass

        # Detect the data header row
        if not found_header:
            first = row[0].strip('"')
            if first == 'Product' or (len(row) > 2 and row[1].strip('"') == 'Product'):
                found_header = True
            continue

        # Parse data rows (after header is detected)
        cleaned = [cell.strip().strip('"') for cell in row]

        # Skip rows with too few columns
        if len(cleaned) < 14:
            continue

        # Skip summary/total/balance rows
        try:
            if not cleaned[2]:  # Symbol column must be populated
                continue

            # Column mapping (from actual CSV structure):
            # [0] Group name, [1] Product, [2] Symbol, [3] Name, [4] Quantity,
            # [5] Last Price, [6] Currency, [10] Total Book Cost,
            # [11] Total Market Value, [13] Average Cost
            symbol = cleaned[2]
            currency = cleaned[6] if len(cleaned) > 6 else 'CAD'

            # Skip empty symbols
            if not symbol:
                continue

            try:
                quantity = Decimal(cleaned[4])
            except Exception:
                continue

            # Skip zero-quantity positions
            if quantity == 0:
                continue

            def _to_decimal(val: str) -> Decimal:
                v = val.replace(',', '').strip()
                if v in ('', '-', '--', 'N/A'):
                    return Decimal('0')
                return Decimal(v)

            market_price = _to_decimal(cleaned[5])
            total_book_cost = _to_decimal(cleaned[10])
            total_market_value = _to_decimal(cleaned[11])
            average_cost = _to_decimal(cleaned[13])

            # Calculate cost_basis_per_unit
            if quantity > 0:
                cost_basis_per_unit = (total_book_cost / quantity).quantize(
                    Decimal('0.0001')
                )
            else:
                cost_basis_per_unit = average_cost

            positions.append({
                'account_id': account_id or '',
                'symbol': symbol,
                'quantity': quantity,
                'cost_basis_per_unit': cost_basis_per_unit,
                'total_cost_basis': total_book_cost,
                'market_price': market_price,
                'market_value': total_market_value,
                'currency': currency,
                'snapshot_date': snapshot_date,
            })
        except (ValueError, IndexError) as e:
            logger.warning(f"Error parsing row {row_num}: {e}")
            continue

    logger.info(
        f"Parsed {len(positions)} positions from Holdings CSV "
        f"(snapshot: {snapshot_date})"
    )
    return positions


def _parse_date(date_str: str) -> date:
    """Parse a date string like 'January 1, 2026' into a date object."""
    # Remove comma and parse: "January 1 2026" -> strptime
    cleaned = date_str.replace(',', '').strip()
    return datetime.strptime(cleaned, '%B %d %Y').date()


def import_holdings_snapshot(
    file_path: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Import a Holdings CSV snapshot into PositionHistory.

    For each (account, symbol) in the snapshot:
    - If a current PositionHistory exists (date_to IS NULL), close it by
      setting date_to to the day before the snapshot date.
    - Create a new PositionHistory row for the snapshot date.

    Args:
        file_path: Path to the Holdings CSV file.
        dry_run: If True, only parse and report.

    Returns:
        Tuple of (created_count, closed_count).
    """
    positions = parse_holdings_csv(file_path)

    if not positions:
        logger.warning(f"No positions found in {file_path}")
        return 0, 0

    snapshot_date = positions[0]['snapshot_date']
    if not snapshot_date:
        logger.error(f"Could not determine snapshot date from {file_path}")
        return 0, 0

    if dry_run:
        logger.info(
            f"Dry run: would import {len(positions)} positions "
            f"for snapshot date {snapshot_date}"
        )
        for pos in positions[:10]:
            logger.info(
                f"  {pos['account_id']} {pos['symbol']}: "
                f"{pos['quantity']} @ {pos['market_price']}"
            )
        return 0, 0

    created = 0
    closed = 0

    from django.db import transaction

    with transaction.atomic():
        for pos in positions:
            account, _ = Account.objects.get_or_create(
                account_id=pos['account_id'],
                defaults={'name': pos['account_id'], 'broker': 'RBC'},
            )

            # Skip if already imported for this exact date
            existing = PositionHistory.objects.filter(
                account=account,
                symbol=pos['symbol'],
                currency=pos['currency'],
                date_from=snapshot_date,
            ).first()
            if existing:
                # Already have a snapshot for this date — skip duplicate
                logger.debug(
                    f"Skipping duplicate: {account.account_id} {pos['symbol']} "
                    f"{pos['currency']} {snapshot_date}"
                )
                continue

            # Close any open position for this (account, symbol, currency)
            open_pos = PositionHistory.objects.filter(
                account=account,
                symbol=pos['symbol'],
                currency=pos['currency'],
                date_to__isnull=True,
            ).first()

            if open_pos:
                date_to = snapshot_date - timedelta(days=1)
                if date_to >= open_pos.date_from:
                    open_pos.date_to = date_to
                    open_pos.save(update_fields=['date_to'])
                    closed += 1

            # Create new position snapshot
            PositionHistory.objects.create(
                owner=account.owner,
                account=account,
                symbol=pos['symbol'],
                quantity=pos['quantity'],
                cost_basis_per_unit=pos['cost_basis_per_unit'],
                total_cost_basis=pos['total_cost_basis'],
                currency=pos['currency'],
                market_price=pos['market_price'],
                market_value=pos['market_value'],
                date_from=snapshot_date,
                date_to=None,
            )
            created += 1

    logger.info(
        f"Imported Holdings snapshot {snapshot_date}: "
        f"{created} created, {closed} closed"
    )
    return created, closed
