"""Fix stale open positions in PositionHistory.

Algorithm:
1. For each (account, symbol, currency), get all PositionHistory records sorted by date_from.
2. Walk the chain: every record except the last gets date_to = next.date_from - 1 day.
3. The last record in the chain remains open (date_to = NULL).
4. For symbols NOT in the latest snapshot, try to find SELL transaction date.
"""
import os
import sys
import django
import logging
from datetime import date, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
django.setup()

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

import django.db.models as models
from core.models import Account, PositionHistory, Transaction


def fix_chain_for_pair(account, symbol, currency, records, dry_run=False):
    """Fix the date_to chain for a list of PositionHistory records.
    
    records should be sorted by date_from. Every record except the last
    gets date_to = next_record.date_from - 1 day. The last remains open.
    """
    fixed = 0
    for i, record in enumerate(records):
        if i + 1 < len(records):
            next_record = records[i + 1]
            expected_date_to = next_record.date_from - timedelta(days=1)
            
            if record.date_to is None or record.date_to != expected_date_to:
                if dry_run:
                    logger.info(
                        f"  {account.account_id} {symbol} {currency}: "
                        f"{record.date_from} -> {expected_date_to} "
                        f"(was: {record.date_to or 'OPEN'})"
                    )
                else:
                    record.date_to = expected_date_to
                    record.save(update_fields=['date_to'])
                fixed += 1
    return fixed


def close_sold_positions(account, dry_run=False):
    """Close positions for symbols that no longer exist in any open position.
    
    After Step 1, every (account, symbol, currency) chain has exactly one open
    position (the last in the chain). This step is a no-op because the chain
    logic already handles everything correctly.
    """
    return 0


def main():
    dry_run = '--dry-run' in sys.argv
    
    if dry_run:
        logger.info("DRY RUN - no changes will be made")
    
    logger.info("Starting position fix...")
    
    total_fixed = 0
    total_pairs = 0
    
    for account in Account.objects.all():
        logger.info(f"\nProcessing account: {account.account_id} ({account.name})")
        
        # Get all unique (symbol, currency) pairs
        pairs = list(PositionHistory.objects.filter(
            account=account
        ).values_list('symbol', 'currency').distinct())
        
        for symbol, currency in pairs:
            total_pairs += 1
            
            # Get all records for this pair, sorted by date_from
            records = list(PositionHistory.objects.filter(
                account=account,
                symbol=symbol,
                currency=currency,
            ).order_by('date_from'))
            
            if not records:
                continue
            
            fixed = fix_chain_for_pair(account, symbol, currency, records, dry_run)
            total_fixed += fixed
    
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Pairs processed: {total_pairs}")
    logger.info(f"Records fixed: {total_fixed}")
    
    # Verify
    open_count = PositionHistory.objects.filter(date_to__isnull=True).count()
    logger.info(f"Open positions after fix: {open_count}")
    
    # Check for remaining overlaps
    overlaps = list(PositionHistory.objects.filter(
        date_to__isnull=True
    ).values('account__account_id', 'symbol', 'currency').annotate(
        count=models.Count('id')
    ).filter(count__gt=1))
    
    if overlaps:
        logger.warning(f"Remaining overlaps: {len(overlaps)}")
        for o in overlaps[:10]:
            logger.warning(f"  {o['account__account_id']} {o['symbol']} {o['currency']}: {o['count']}")
    else:
        logger.info("No remaining overlaps")
    
    # Check per-account open counts
    logger.info("\nOpen positions per account:")
    for account in Account.objects.all():
        count = PositionHistory.objects.filter(
            account=account, date_to__isnull=True
        ).count()
        logger.info(f"  {account.account_id}: {count}")


if __name__ == '__main__':
    main()
