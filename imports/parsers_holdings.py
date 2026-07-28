"""Parser for RBC Holdings CSV exports.

Converts Holdings CSV snapshots into PositionHistory records.
"""

import csv
import logging
import os
import re
from datetime import date, timedelta
from datetime import datetime
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
    filename = os.path.basename(file_path)

    try:
        with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.reader(csvfile)

            for row_num, row in enumerate(reader, start=1):
                if not row or all(cell.strip() == '' for cell in row):
                    continue

                # Extract snapshot date from header
                if not snapshot_date:
                    # Join all cells, replace commas, normalize whitespace
                    joined = ' '.join(row).replace(',', ' ')
                    joined = re.sub(r'\s+', ' ', joined)
                    # Matches: "January 1 2026"
                    date_match = re.search(
                        r'([A-Za-z]+ \d{1,2} \d{4})',
                        joined,
                    )
                    if date_match:
                        try:
                            snapshot_date = datetime.strptime(
                                date_match.group(1).strip(),
                                '%B %d %Y',
                            ).date()
                        except Exception:
                            pass

                # Detect the data header row
                if not found_header:
                    first = row[0].strip().strip('"')
                    if first == 'Account':
                        found_header = True
                    continue

                # Parse data rows
                cleaned = [cell.strip().strip('"') for cell in row]

                # Skip summary/total rows
                if not cleaned[0].isdigit():
                    continue

                # Skip practice accounts
                if cleaned[0].upper().startswith('PRAC'):
                    continue

                account_id = cleaned[0]
                symbol = cleaned[5]  # Symbol/Name column
                currency = cleaned[8] if len(cleaned) > 8 else 'CAD'

                # Skip empty symbols
                if not symbol:
                    continue

                try:
                    quantity = Decimal(cleaned[6])
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

                market_price = _to_decimal(cleaned[7])
                total_book_cost = _to_decimal(cleaned[11])
                total_market_value = _to_decimal(cleaned[12])
                average_cost = _to_decimal(cleaned[15])

                # Calculate cost_basis_per_unit
                if quantity > 0:
                    cost_basis_per_unit = (total_book_cost / quantity).quantize(
                        Decimal('0.0001')
                    )
                else:
                    cost_basis_per_unit = average_cost

                positions.append({
                    'account_id': account_id,
                    'symbol': symbol,
                    'quantity': quantity,
                    'cost_basis_per_unit': cost_basis_per_unit,
                    'total_cost_basis': total_book_cost,
                    'market_price': market_price,
                    'market_value': total_market_value,
                    'currency': currency,
                    'snapshot_date': snapshot_date,
                })

    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")

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
