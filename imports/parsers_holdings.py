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
from core.utils.account_fingerprint import lookup_account

logger = logging.getLogger(__name__)


def _detect_column_mapping(row: list[str]) -> dict | None:
    """Detect RBC Holdings CSV column layout by scanning the header row.

    RBC has two known formats:
    OLD (single-account, ~23 cols): Group | Product | Symbol | Name | Qty | Price | Currency | ...
        Header row starts with: "Canadian Holdings", "Product", "SYMBOL", ...
        → Symbol at col 2, Quantity at col 4, Price at col 5, Currency at col 6
        → Book Cost at col 10, Market Value at col 11, Avg Cost at col 13

    NEW (multi-account Goal, ~26 cols): Account | AccountType | Nickname | SettlementCurrency | Product | Symbol | Qty | Price | Currency | ...
        Header row starts with: "90000007", "Investment Joint", "TAXABLE_CAR", "CAD Holdings", "Product", "Symbol/Name", ...
        → Symbol at col 5, Quantity at col 6, Price at col 7, Currency at col 8
        → Book Cost at col 11, Market Value at col 12, Avg Cost at col 15

    OLD (single-account, ~23 cols): Group | Product | Symbol | Name | Qty | Price | Currency | ...
        Header row starts with: "Canadian Holdings", "Product", "SYMBOL", ...
        → Symbol at col 2, Quantity at col 4, Price at col 5, Currency at col 6
        → Book Cost at col 10, Market Value at col 11, Avg Cost at col 13

    Returns a mapping dict or None if no recognized header found.
    """
    cleaned = [c.strip().strip('"').lower() for c in row]

    # Find the index of "product" column — it's the key discriminator
    product_indices = [i for i, c in enumerate(cleaned) if c == 'product']
    if not product_indices:
        return None

    product_idx = product_indices[0]

    if product_idx == 1:
        # OLD format: Product at col 1.
        # Two sub-formats:
        #   REAL RBC: col0='' (empty) col1=Product col2=Symbol col3=Name col4=Quantity
        #              col9=Total Book Cost col10=Total Market Value col13=Average Cost
        #   TEST/ALT: col0=Group col1=Product col2=Symbol col3=Name col4=Quantity
        #              col10=Total Book Cost col11=Total Market Value col13=Average Cost
        # Detect sub-format by checking col0 of the header row:
        #   Empty/blank col0 = real RBC (book_cost at col9)
        #   Non-empty col0 = test/alt (book_cost at col10)
        col0 = cleaned[0].strip().strip('"').lower()
        if col0 == '' or col0 == '(empty)':
            # Real RBC format
            book_cost_off = 8   # col 9
            market_val_off = 9  # col 10
        else:
            # Test/alt format with Group column at col0
            book_cost_off = 9   # col 10
            market_val_off = 10 # col 11
        return {
            'format': 'old',
            'account_col': None,
            'symbol_col': product_idx + 1,   # col 2 = Symbol
            'qty_col': product_idx + 3,      # col 4 = Quantity
            'price_col': product_idx + 4,    # col 5 = Last Price
            'currency_col': product_idx + 5, # col 6 = Currency
            'book_cost_col': product_idx + book_cost_off,
            'market_value_col': product_idx + market_val_off,
            'avg_cost_col': product_idx + 12,    # col 13 = Average Cost
            'min_cols': product_idx + 13,
        }
    elif product_idx >= 3 and product_idx <= 5:
        # NEW format: "Account", "AccountType", "Nickname", "SettlementCurrency", "Product", "Symbol/Name", "Quantity"
        # Check if this is a "full" NEW format (18+ cols with avg_cost) or a "compact" NEW format (15 cols, no avg_cost)
        # Compact format: columns 0-14 (ends at Unrealized Gain/Loss %)
        # Full format: columns 0-25+ (includes Average Cost at col 15+)
        # We determine this by checking if col 15 exists in the row later
        return {
            'format': 'new',
            'account_col': product_idx - 4,
            'symbol_col': product_idx + 1,
            'qty_col': product_idx + 2,
            'price_col': product_idx + 3,
            'currency_col': product_idx + 4,
            'book_cost_col': product_idx + 7,   # col 11 = Total Book Cost
            'market_value_col': product_idx + 8, # col 12 = Total Market Value
            'avg_cost_col': product_idx + 11,    # col 15 = Average Cost (may not exist)
            'min_cols': product_idx + 9,          # min: need up to market_value_col (col 12)
            'has_avg_cost_col': True,            # flag for post-detection
        }
    return None


def parse_holdings_csv(file_path: str) -> list[dict]:
    """Parse an RBC Holdings CSV export.

    Supports both old single-account format and new multi-account "Goal" format.

    Returns a list of dicts, one per row, containing:
        account_id, symbol, quantity, cost_basis_per_unit,
        total_cost_basis, market_price, market_value, currency,
        snapshot_date (extracted from file header).
    """
    positions = []
    snapshot_date = None
    found_header = False
    account_id = None
    column_mapping = None
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

    # Detect delimiter by scanning actual data rows (not header/metadata lines)
    # RBC Holdings exports can be tab-delimited (older format) or comma-delimited
    # We look for lines that start with a numeric account ID (data rows)
    delimiter = ','  # default fallback
    for line in raw_content.split('\n'):
        stripped = line.strip()
        if stripped and re.match(r'^\s*\d{5,}\s', stripped):
            # This looks like a data row — check its delimiter
            if '\t' in stripped:
                delimiter = '\t'
            break

    reader = csv.reader(io.StringIO(raw_content), delimiter=delimiter)

    # --- Extract account_id from filename (old single-account format) ---
    # Pattern A: "Holdings 90000004 January 1, 2026.csv"
    acct_from_filename = re.search(r'Holdings\s+(\d{5,})', filename)
    if acct_from_filename:
        account_id = acct_from_filename.group(1)
    # Pattern B: "tmpXXXXX_90000001_Holdings.csv" — any 5+ digit sequence
    if not account_id:
        acct_match = re.search(r'(\d{5,})', filename)
        if acct_match:
            account_id = acct_match.group(0)

    # Also check for "Account: XXXX" in header lines
    header_acct_match = re.search(r'Account:\s*(\d{5,})', raw_content)
    if header_acct_match:
        account_id = header_acct_match.group(1)
    
    # Skip PRACEF practice accounts at file level (the file name can carry
    # the account number and must not be logged).
    if account_id and account_id.startswith('PRACEF'):
        logger.info("Skipping PRACEF practice account file")
        return [], None

    # Fallback: scan raw content for first data row starting with a digit
    # This handles CSVs that have no Account: line and no numeric filename
    if not account_id and raw_content:
        lines = raw_content.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped and re.match(r'\s*\d', stripped):
                acct_match = re.match(r'(\d{5,})', stripped)
                if acct_match:
                    account_id = acct_match.group(1)
                    break

    # --- Now parse the CSV data rows ---

    for row_num, row in enumerate(reader, start=1):
        if not row or all(cell.strip() == '' for cell in row):
            continue

        # --- Extract snapshot date from header ---
        # ONLY extract date BEFORE the data header row is found.
        # After the header, rows contain dividend ex-dates (e.g. "Sep 4, 2026")
        # that would be incorrectly picked up by the regex.
        if not found_header:
            joined = ' '.join(row).replace(',', ' ')
            joined = re.sub(r'\s+', ' ', joined)
            date_match = re.search(
                r'([A-Za-z]+ \d{1,2},? \d{4})',
                joined,
            )
            if date_match:
                try:
                    cleaned_date = date_match.group(1).replace(',', '').strip()
                    try:
                        candidate = datetime.strptime(cleaned_date, '%b %d %Y').date()
                    except ValueError:
                        try:
                            candidate = datetime.strptime(cleaned_date, '%B %d %Y').date()
                        except ValueError:
                            candidate = None
                    if candidate:
                        # Only update if date is reasonable (>2000 and not the footnote date)
                        if candidate.year > 2000 and candidate.year < 2030 and candidate != date(2015, 9, 30):
                            snapshot_date = candidate
                except Exception:
                    pass

            # --- Detect the data header row (smart format detection) ---
            if not found_header:
                column_mapping = _detect_column_mapping(row)
                if column_mapping:
                    found_header = True
                    logger.debug(f"Detected {column_mapping['format']} format at row {row_num}")
            continue

        if not column_mapping:
            # Could not detect format — skip row
            continue

        # Parse data rows
        cleaned = [cell.strip().strip('"') for cell in row]

        # Skip rows with too few columns
        if len(cleaned) < column_mapping['min_cols']:
            continue

        try:
            # --- Account ID ---
            if column_mapping['format'] == 'new':
                # New format: account_id is in a data column
                acct_col = column_mapping['account_col']
                raw_acct = cleaned[acct_col].strip()
                # Skip PRACEF group accounts (multi-account goal containers)
                if raw_acct.startswith('PRACEF'):
                    continue
                # Extract numeric account ID
                acct_match = re.match(r'(\d{5,})', raw_acct)
                if acct_match:
                    row_account_id = acct_match.group(1)
                else:
                    row_account_id = None
                # Use row account if available, otherwise fall back to file-level account
                effective_account_id = row_account_id or account_id
            else:
                # Old format: account from filename or fallback sources
                effective_account_id = account_id

            # If no account_id found, use 'UNKNOWN' — this allows parse_holdings_csv
            # (used by tests) to return positions even without account context.
            # import_holdings_snapshot later resolves this via Account.objects.get_or_create.
            if not effective_account_id:
                effective_account_id = 'UNKNOWN'

            # --- Symbol ---
            symbol = cleaned[column_mapping['symbol_col']].strip()
            if not symbol:
                continue
            symbol = symbol.upper()

            # --- Quantity ---
            qty_str = cleaned[column_mapping['qty_col']].strip()
            try:
                quantity = Decimal(qty_str)
            except Exception:
                continue

            # Skip zero-quantity positions
            if quantity == 0:
                continue

            def _to_decimal(val: str) -> Decimal:
                v = val.replace(',', '').strip()
                if v in ('', '-', '--', 'N/A'):
                    return Decimal('0')
                # Strip trailing % sign (e.g. "0.87%" -> "0.87")
                v = v.rstrip('%')
                return Decimal(v)

            # --- Extract fields using detected column mapping ---
            market_price = _to_decimal(cleaned[column_mapping['price_col']])
            total_book_cost = _to_decimal(cleaned[column_mapping['book_cost_col']])
            total_market_value = _to_decimal(cleaned[column_mapping['market_value_col']])

            # avg_cost column may not exist in compact format (15 cols, no col 15)
            avg_cost_col = column_mapping['avg_cost_col']
            if avg_cost_col < len(cleaned):
                average_cost = _to_decimal(cleaned[avg_cost_col])
            else:
                average_cost = None

            # Currency: use per-security currency column (CAD/USD)
            currency = cleaned[column_mapping['currency_col']].strip()
            if currency.upper() in ('CAD', 'CAD HOLDINGS'):
                currency = 'CAD'
            elif currency.upper() in ('USD', 'USD HOLDINGS'):
                currency = 'USD'
            elif currency.upper() in ('CAD HOLDINGS', 'CAD'):
                currency = 'CAD'
            elif currency.upper() in ('USD HOLDINGS', 'USD'):
                currency = 'USD'
            else:
                # Default based on settlement currency column (one left of currency)
                settlement_col = column_mapping['currency_col'] - 1
                if len(cleaned) > settlement_col:
                    settlement = cleaned[settlement_col].strip().upper()
                    currency = 'USD' if 'USD' in settlement else 'CAD'
                else:
                    currency = 'CAD'

            # Calculate cost_basis_per_unit
            # Prefer computed from total_book_cost / quantity, fall back to average_cost column
            if quantity > 0:
                cost_basis_per_unit = (total_book_cost / quantity).quantize(
                    Decimal('0.0001')
                )
            elif average_cost is not None:
                cost_basis_per_unit = average_cost
            else:
                cost_basis_per_unit = Decimal('0')

            positions.append({
                'account_id': effective_account_id,
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


def holdings_import_report(file_path: str) -> dict:
    """Parse a Holdings CSV and report what an import would touch.

    Returns a dict with:
      - ``positions``: the parsed position dicts (as :func:`parse_holdings_csv`);
      - ``snapshot_date``: the snapshot date, or None;
      - ``sections``: one entry per (account name, currency) group with the
        holdings count.

    The report carries names and counts only — never quantities, prices or
    account digits (import-checks plan step 13b). Unresolvable, UNKNOWN and
    PRACEF account ids are labelled ``UNKNOWN_ACCOUNT``.
    """
    positions = parse_holdings_csv(file_path)
    snapshot_date = positions[0]['snapshot_date'] if positions else None

    name_cache: dict[str, str] = {}
    counts: dict[tuple[str, str], int] = {}
    for pos in positions:
        acct_id = (pos.get('account_id') or '').strip()
        if not acct_id or acct_id == 'UNKNOWN' or acct_id.startswith('PRACEF'):
            label = 'UNKNOWN_ACCOUNT'
        else:
            if acct_id not in name_cache:
                account = lookup_account(acct_id)
                name_cache[acct_id] = (
                    account.name if account is not None else 'UNKNOWN_ACCOUNT'
                )
            label = name_cache[acct_id]
        key = (label, pos.get('currency') or 'CAD')
        counts[key] = counts.get(key, 0) + 1

    sections = [
        {'account': account, 'currency': currency, 'holdings': n}
        for (account, currency), n in sorted(counts.items())
    ]
    return {
        'positions': positions,
        'snapshot_date': snapshot_date,
        'sections': sections,
    }


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
        Tuple of (created_count, closed_count, skipped_count).
    """
    positions = parse_holdings_csv(file_path)

    if not positions:
        logger.warning("No positions found in holdings file")
        return 0, 0

    snapshot_date = positions[0]['snapshot_date']
    if not snapshot_date:
        logger.error("Could not determine snapshot date from holdings file")
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
    skipped = 0

    from django.db import transaction

    with transaction.atomic():
        for pos in positions:
            acct_id = pos['account_id']
            
            # Skip empty, UNKNOWN, or PRACEF account IDs
            if not acct_id or acct_id == 'UNKNOWN' or acct_id.startswith('PRACEF'):
                logger.warning(
                    f"Skipping position {pos['symbol']}: invalid account_id='{acct_id}'"
                )
                skipped += 1
                continue
            
            # Look up existing account by fingerprint — do NOT auto-create
            account = lookup_account(acct_id)
            if account is None:
                logger.warning(
                    f"Account with unknown fingerprint — skipping position {pos['symbol']}. "
                    f"Run set_account_fingerprint for this account first."
                )
                skipped += 1
                continue

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
        f"{created} created, {closed} closed, {skipped} skipped"
    )
    return created, closed
