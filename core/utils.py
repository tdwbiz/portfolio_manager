from collections import deque, defaultdict
from decimal import Decimal
import yfinance as yf
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta, date
from core.models import Transaction
import logging
import calendar

logger = logging.getLogger(__name__)


POSITION_TRANSACTION_TYPES = ('BUY', 'SELL')


def _generate_monthly_dates(start_date: date, end_date: date) -> list[date]:
    """Generate a list of month-end dates from start_date to end_date (inclusive)."""
    dates = []
    current = start_date.replace(day=1)
    while current <= end_date:
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1, day=1)
        else:
            current = current.replace(month=current.month + 1, day=1)
        # Last day of the previous month (current is now the 1st of the next month)
        dates.append(current - timedelta(days=1))
    return dates


def compute_portfolio_value_series(
    freq: str = 'monthly',
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute portfolio market value over time with historical prices.

    Args:
        freq: Frequency of data points ('monthly' or 'weekly'). Defaults to 'monthly'.
        date_from: Optional ISO date string to clip the start of the series.
        date_to: Optional ISO date string to clip the end of the series.

    Returns:
        List of dicts with 'date' (ISO string) and 'market_value_cad' (Decimal).
    """
    logger.info("compute_portfolio_value_series called")
    try:
        # Get all transactions ordered by date
        transactions = Transaction.objects.filter(type__in=POSITION_TRANSACTION_TYPES).order_by('date', 'id')

        if not transactions.exists():
            logger.info("No BUY/SELL transactions found, returning empty series")
            return []

        # Determine date range
        start_date = transactions.first().date
        end_date = timezone.now().date()

        # Apply date clipping
        if date_from:
            try:
                parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
                start_date = max(start_date, parsed_from)
            except ValueError:
                pass

        if date_to:
            try:
                parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
                end_date = min(end_date, parsed_to)
            except ValueError:
                pass

        logger.info(f"Date range: {start_date} to {end_date}")

        # Generate monthly period-end dates
        dates = _generate_monthly_dates(start_date, end_date)

        # Collect all unique symbols ever held
        all_symbols = list(set(tx.symbol for tx in transactions))
        logger.info(f"Found {len(all_symbols)} unique symbols: {all_symbols}")

        # Fetch historical prices from cache or yfinance
        cache_key = 'hist_prices'
        prices_df = cache.get(cache_key)

        if prices_df is None or (hasattr(prices_df, 'empty') and prices_df.empty):
            # Add USDCAD for currency conversion
            symbols_with_usdcad = list(set(all_symbols + ['USDCAD=X']))
            logger.info(f"Fetching historical data from yfinance for {len(symbols_with_usdcad)} symbols")

            try:
                prices_df = yf.download(
                    ' '.join(symbols_with_usdcad),
                    start=start_date.strftime('%Y-%m-%d'),
                    end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d'),
                    auto_adjust=True,
                    progress=False
                )['Close']
                cache.set(cache_key, prices_df, settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
                logger.info(f"✓ Historical data fetched and cached")
            except Exception as e:
                logger.error(f"✗ Error fetching historical data: {type(e).__name__}: {e}")
                prices_df = None
        else:
            logger.info("Using cached historical data")

        # Calculate market value for each date point
        results = []

        for tx_date in dates:
            holdings = {}

            # Replay transactions up to this date
            for tx in transactions.filter(date__lte=tx_date):
                symbol = tx.symbol
                if tx.type == 'BUY':
                    holdings[symbol] = holdings.get(symbol, Decimal('0')) + tx.quantity
                elif tx.type == 'SELL':
                    holdings[symbol] = holdings.get(symbol, Decimal('0')) - tx.quantity

            # Get unique symbols with positive holdings
            symbols = [s for s, qty in holdings.items() if qty > 0]

            # Calculate market value for this date
            market_value = Decimal('0')

            if prices_df is not None and not prices_df.empty:
                for symbol in symbols:
                    try:
                        # Get price for this symbol on this date
                        if symbol in prices_df.columns:
                            price_series = prices_df[symbol]
                            if not price_series.empty:
                                price = price_series.asof(tx_date)

                                # Handle currency conversion
                                if symbol.endswith('CAD'):
                                    fx_rate = Decimal('1')
                                else:
                                    usdcad_series = prices_df['USDCAD=X']
                                    fx_rate = usdcad_series.asof(tx_date)

                                # Add to market value
                                market_value += Decimal(str(price)) * holdings[symbol] * Decimal(str(fx_rate))
                    except Exception:
                        # Skip symbols with price errors
                        pass

            results.append({
                'date': tx_date.strftime('%Y-%m-%d'),
                'market_value_cad': market_value,
            })

        return results
    except Exception:
        logger.exception("Error in compute_portfolio_value_series")
        return []


def compute_realised_pnl(account_id: str = None) -> list[dict]:
    """Compute realised P&L using FIFO and average-cost methods."""
    qs = Transaction.objects.select_related('account')
    if account_id:
        qs = qs.filter(account__account_id=account_id)

    transactions = qs.filter(type__in=POSITION_TRANSACTION_TYPES).order_by('date', 'id')

    # Group by (symbol, account_id), preserving chronological order
    groups: dict[tuple, list] = {}
    for tx in transactions:
        key = (tx.symbol, tx.account.account_id if tx.account else '')
        groups.setdefault(key, []).append(tx)

    result = []

    for (symbol, acct_id), txs in groups.items():
        fifo_deque: deque = deque()
        total_shares = Decimal('0')
        total_cost = Decimal('0')

        for tx in txs:
            cost = abs(tx.amount_cad)

            if tx.type == 'BUY':
                fifo_deque.append({'qty': tx.quantity, 'cost': cost})
                total_shares += tx.quantity
                total_cost += cost

            elif tx.type == 'SELL':
                sell_amount = abs(tx.amount_cad)
                qty_to_sell = tx.quantity

                # FIFO cost basis
                qty_remaining = qty_to_sell
                matched_cost_fifo = Decimal('0')
                while qty_remaining > 0 and fifo_deque:
                    lot = fifo_deque[0]
                    if lot['qty'] <= qty_remaining:
                        matched_cost_fifo += lot['cost']
                        qty_remaining -= lot['qty']
                        fifo_deque.popleft()
                    else:
                        fraction = qty_remaining / lot['qty']
                        cost_taken = lot['cost'] * fraction
                        matched_cost_fifo += cost_taken
                        lot['cost'] -= cost_taken
                        lot['qty'] -= qty_remaining
                        qty_remaining = Decimal('0')

                # Avg-cost basis
                avg_cost_per_share = total_cost / total_shares if total_shares > 0 else Decimal('0')
                cost_basis_avg = qty_to_sell * avg_cost_per_share

                # Update running avg-cost state
                total_shares -= qty_to_sell
                total_cost -= cost_basis_avg

                result.append({
                    'symbol': symbol,
                    'account_id': acct_id,
                    'sell_date': tx.date,
                    'qty_sold': qty_to_sell,
                    'sell_amount_cad': sell_amount,
                    'cost_basis_fifo': matched_cost_fifo,
                    'cost_basis_avg': cost_basis_avg,
                    'realised_pnl_fifo': sell_amount - matched_cost_fifo,
                    'realised_pnl_avg': sell_amount - cost_basis_avg,
                })

    result.sort(key=lambda x: x['sell_date'], reverse=True)
    return result


def compute_cost_basis_series(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute total invested cost basis over time (monthly series).

    For each monthly date point, sum the amount_cad of all BUY transactions
    (absolute value) up to and including that date, minus the cost of all
    sold shares (approximated as average cost at time of sale, same as the
    avg-cost method in compute_realised_pnl).

    Returns a list of dicts: [{'date': 'YYYY-MM-DD', 'cost_basis_cad': Decimal}]
    Uses the same monthly date grid as compute_portfolio_value_series.
    """
    cache_key = 'cost_basis_series'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        transactions = Transaction.objects.filter(type__in=POSITION_TRANSACTION_TYPES).order_by('date', 'id')

        if not transactions.exists():
            cache.set(cache_key, [], settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
            return []

        # Determine date range
        start_date = transactions.first().date
        end_date = timezone.now().date()

        if date_from:
            try:
                parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
                start_date = max(start_date, parsed_from)
            except ValueError:
                pass

        if date_to:
            try:
                parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
                end_date = min(end_date, parsed_to)
            except ValueError:
                pass

        dates = _generate_monthly_dates(start_date, end_date)

        # Compute cost basis using average-cost method
        total_shares = Decimal('0')
        total_cost = Decimal('0')

        results = []
        for tx_date in dates:
            # Replay transactions up to this date
            for tx in transactions.filter(date__lte=tx_date):
                cost = abs(tx.amount_cad)

                if tx.type == 'BUY':
                    total_shares += tx.quantity
                    total_cost += cost

                elif tx.type == 'SELL':
                    avg_cost_per_share = total_cost / total_shares if total_shares > 0 else Decimal('0')
                    cost_basis_avg = tx.quantity * avg_cost_per_share
                    total_shares -= tx.quantity
                    total_cost -= cost_basis_avg

            results.append({
                'date': tx_date.strftime('%Y-%m-%d'),
                'cost_basis_cad': total_cost,
            })

        cache.set(cache_key, results, settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
        return results
    except Exception:
        logger.exception("Error in compute_cost_basis_series")
        return []


def compute_income_series(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute monthly income (dividends, distributions, interest) over time.

    For each calendar month, sums amount_cad of transactions with type in
    ('DIV', 'DIST', 'INT'). Returns each month as a separate bar value.

    Returns a list of dicts: [{'date': 'YYYY-MM-DD', 'income_cad': Decimal}]
    where 'date' is the last day of the month (matching other series).
    """
    cache_key = 'income_series'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        income_types = ('DIV', 'DIST', 'INT')
        transactions = Transaction.objects.filter(type__in=income_types).order_by('date', 'id')

        if not transactions.exists():
            cache.set(cache_key, [], settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
            return []

        # Determine date range
        start_date = transactions.first().date
        end_date = timezone.now().date()

        if date_from:
            try:
                parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
                if parsed_from < start_date:
                    start_date = parsed_from
                elif parsed_from > end_date:
                    start_date = end_date
            except ValueError:
                pass

        if date_to:
            try:
                parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
                if parsed_to > end_date:
                    end_date = parsed_to
                elif parsed_to < start_date:
                    end_date = start_date
            except ValueError:
                pass

        dates = _generate_monthly_dates(start_date, end_date)

        # Build a lookup of income by month-end date
        income_by_date = defaultdict(lambda: Decimal('0'))
        for tx in transactions:
            # Find the month-end date that this transaction falls into
            tx_month_end = date(tx.date.year, tx.date.month, 1)
            if tx_month_end.month == 12:
                tx_month_end = tx_month_end.replace(year=tx_month_end.year + 1, month=1, day=1)
            else:
                tx_month_end = tx_month_end.replace(month=tx_month_end.month + 1, day=1)
            tx_month_end = tx_month_end - timedelta(days=1)
            income_by_date[tx_month_end] += tx.amount_cad

        results = []
        for tx_date in dates:
            results.append({
                'date': tx_date.strftime('%Y-%m-%d'),
                'income_cad': income_by_date.get(tx_date, Decimal('0')),
            })

        cache.set(cache_key, results, settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
        return results
    except Exception:
        logger.exception("Error in compute_income_series")
        return []


def compute_series_by_account(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, list[dict]]:
    """Compute monthly market value series for each account independently.

    Returns a dict keyed by account_id, each value being a list of
    {'date': 'YYYY-MM-DD', 'market_value_cad': Decimal} dicts.
    Uses the same historical price data as compute_portfolio_value_series
    (served from cache if already fetched).
    """
    try:
        # Get all accounts that have at least one BUY transaction
        accounts_with_buys = Transaction.objects.filter(
            type='BUY'
        ).values_list('account__account_id', flat=True).distinct()

        if not accounts_with_buys:
            return {}

        results = {}
        for account_id in accounts_with_buys:
            # Get transactions for this account
            account_txs = Transaction.objects.filter(
                account__account_id=account_id,
                type__in=POSITION_TRANSACTION_TYPES
            ).order_by('date', 'id')

            if not account_txs.exists():
                continue

            # Determine date range
            start_date = account_txs.first().date
            end_date = timezone.now().date()

            if date_from:
                try:
                    parsed_from = datetime.strptime(date_from, '%Y-%m-%d').date()
                    start_date = max(start_date, parsed_from)
                except ValueError:
                    pass

            if date_to:
                try:
                    parsed_to = datetime.strptime(date_to, '%Y-%m-%d').date()
                    end_date = min(end_date, parsed_to)
                except ValueError:
                    pass

            dates = _generate_monthly_dates(start_date, end_date)

            # Fetch historical prices (reuse cache if available)
            cache_key = 'hist_prices'
            prices_df = cache.get(cache_key)

            if prices_df is None or (hasattr(prices_df, 'empty') and prices_df.empty):
                all_symbols = list(set(tx.symbol for tx in account_txs))
                symbols_with_usdcad = list(set(all_symbols + ['USDCAD=X']))

                try:
                    prices_df = yf.download(
                        ' '.join(symbols_with_usdcad),
                        start=start_date.strftime('%Y-%m-%d'),
                        end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d'),
                        auto_adjust=True,
                        progress=False
                    )['Close']
                    cache.set(cache_key, prices_df, settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
                except Exception as e:
                    logger.error(f"Error fetching historical data for account {account_id}: {e}")
                    prices_df = None

            # Calculate market value for each date point
            account_results = []
            for tx_date in dates:
                holdings = {}

                for tx in account_txs.filter(date__lte=tx_date):
                    symbol = tx.symbol
                    if tx.type == 'BUY':
                        holdings[symbol] = holdings.get(symbol, Decimal('0')) + tx.quantity
                    elif tx.type == 'SELL':
                        holdings[symbol] = holdings.get(symbol, Decimal('0')) - tx.quantity

                symbols = [s for s, qty in holdings.items() if qty > 0]
                market_value = Decimal('0')

                if prices_df is not None and not prices_df.empty:
                    for symbol in symbols:
                        try:
                            if symbol in prices_df.columns:
                                price_series = prices_df[symbol]
                                if not price_series.empty:
                                    price = price_series.asof(tx_date)

                                    if symbol.endswith('CAD'):
                                        fx_rate = Decimal('1')
                                    else:
                                        usdcad_series = prices_df['USDCAD=X']
                                        fx_rate = usdcad_series.asof(tx_date)

                                    market_value += Decimal(str(price)) * holdings[symbol] * Decimal(str(fx_rate))
                        except Exception:
                            pass

                account_results.append({
                    'date': tx_date.strftime('%Y-%m-%d'),
                    'market_value_cad': market_value,
                })

            results[str(account_id)] = account_results

        return results
    except Exception:
        logger.exception("Error in compute_series_by_account")
        return {}


def compute_positions(broker=None):
    """Compute positions from Transaction queryset."""
    qs = Transaction.objects.select_related('statement', 'account').all()
    if broker:
        qs = qs.filter(statement__source=broker)

    # Group by (symbol, broker, account)
    positions = {}

    for tx in qs:
        account_name = tx.account.name if tx.account else ''
        key = (tx.symbol, tx.statement.source, account_name)
        if key not in positions:
            positions[key] = {
                'symbol': tx.symbol,
                'broker': tx.statement.source,
                'account_name': account_name,
                'currency': tx.currency,
                'total_shares': Decimal('0'),
                'total_cost_cad': Decimal('0'),
                'buy_qty': Decimal('0'),
                'buy_cost_cad': Decimal('0'),
            }

        pos = positions[key]

        if tx.type == 'BUY':
            pos['total_shares'] += tx.quantity
            pos['total_cost_cad'] += tx.amount_cad
            pos['buy_qty'] += tx.quantity
            pos['buy_cost_cad'] += tx.amount_cad
        elif tx.type == 'SELL':
            pos['total_shares'] -= tx.quantity
            pos['total_cost_cad'] -= tx.amount_cad

    # Convert to list of dicts
    result = []
    for key, pos in positions.items():
        if pos['total_shares'] > 0:
            avg_cost = pos['buy_cost_cad'] / pos['buy_qty'] if pos['buy_qty'] else Decimal('0')
            result.append({
                'symbol': pos['symbol'],
                'broker': pos['broker'],
                'account_name': pos['account_name'],
                'currency': pos['currency'],
                'total_shares': pos['total_shares'],
                'avg_cost_cad': avg_cost,
                'total_cost_cad': pos['total_cost_cad'],
            })

    # Sort by broker, account name, symbol
    result.sort(key=lambda x: (x['broker'], x['account_name'], x['symbol'] or ''))
    return result


def fetch_last_prices(symbols: list[str], cache_timeout: int | None = None) -> dict[str, Decimal]:
    """Fetch latest prices for given symbols using yfinance."""
    cache_timeout = cache_timeout or settings.PRICE_CACHE_TIMEOUT
    logger.info(f"fetch_last_prices called with {len(symbols)} symbols: {symbols}")

    to_fetch = list(symbols)
    if 'USDCAD=X' not in to_fetch:
        to_fetch.append('USDCAD=X')

    cached_prices = {}
    uncached_symbols = []

    for sym in to_fetch:
        price = cache.get(f'price_{sym}')
        if price is not None:
            logger.debug(f"Using cached price for {sym}: {price}")
            cached_prices[sym] = price
        else:
            uncached_symbols.append(sym)

    if uncached_symbols:
        logger.info(f"Fetching prices from yfinance for {len(uncached_symbols)} symbols: {uncached_symbols}")
        try:
            tickers = yf.Tickers(' '.join(uncached_symbols))
            logger.debug(f"yfinance Tickers object created successfully")

            for sym in uncached_symbols:
                try:
                    ticker_obj = tickers.tickers[sym]
                    price = ticker_obj.fast_info.last_price
                    if price is not None:
                        price = Decimal(str(price))
                        cache.set(f'price_{sym}', price, cache_timeout)
                        cached_prices[sym] = price
                        logger.info(f"✓ Fetched price for {sym}: {price}")
                    else:
                        logger.warning(f"✗ No price available for {sym} (fast_info.last_price is None)")
                except Exception as e:
                    logger.error(f"✗ Error fetching price for {sym}: {type(e).__name__}: {e}")
        except Exception as e:
            logger.error(f"✗ Error creating yfinance Tickers object: {type(e).__name__}: {e}")
    else:
        logger.info("All prices served from cache")

    logger.info(f"Returning {len(cached_prices)} prices: {list(cached_prices.keys())}")
    return cached_prices


def enrich_positions_with_prices(positions: list[dict], prices: dict) -> list[dict]:
    """Enrich positions with market prices and calculate P&L."""
    usdcad = prices.get('USDCAD=X', Decimal('1'))

    enriched = []
    for pos in positions:
        last_price = prices.get(pos['symbol'])

        fx = usdcad if pos['currency'] != 'CAD' else Decimal('1')

        market_value_cad = None
        if last_price is not None:
            market_value_cad = last_price * pos['total_shares'] * fx

        unrealised_pnl_cad = None
        if market_value_cad is not None:
            unrealised_pnl_cad = market_value_cad - pos['total_cost_cad']

        unrealised_pnl_pct = None
        if unrealised_pnl_cad is not None and pos['total_cost_cad'] != 0:
            unrealised_pnl_pct = (unrealised_pnl_cad / pos['total_cost_cad'] * 100)

        enriched.append({
            **pos,
            'last_price': last_price,
            'market_value_cad': market_value_cad,
            'unrealised_pnl_cad': unrealised_pnl_cad,
            'unrealised_pnl_pct': unrealised_pnl_pct,
        })

    return enriched
