from collections import deque
from decimal import Decimal
import yfinance as yf
from django.core.cache import cache
from core.models import Transaction

def compute_portfolio_value_series(freq='monthly'):
    """Compute portfolio market value over time with historical prices."""
    try:
        # Get all transactions ordered by date
        transactions = Transaction.objects.filter(type__in=['BUY', 'SELL']).order_by('date', 'id')
        
        if not transactions.exists():
            return []

        # Determine date range
        start_date = transactions.first().date
        end_date = timezone.now().date()
        
        # Generate monthly period-end dates
        dates = []
        current = start_date.replace(day=1)
        while current <= end_date:
            current = current.replace(day=28) + timedelta(days=4)  # Move to next month
            dates.append(current.replace(day=current.day-1))  # Last day of month
        
        # Compute holdings for each date point
        results = []
        
        for date in dates:
            holdings = {}
            
            # Replay transactions up to this date
            for tx in transactions.filter(date__lte=date):
                symbol = tx.symbol
                
                if tx.type == 'BUY':
                    holdings[symbol] = holdings.get(symbol, Decimal('0')) + tx.quantity
                elif tx.type == 'SELL':
                    holdings[symbol] = holdings.get(symbol, Decimal('0')) - tx.quantity
            
            # Get unique symbols with holdings
            symbols = [s for s, qty in holdings.items() if qty > 0]
            
            # Fetch historical prices from cache or yfinance
            cache_key = 'hist_prices'
            prices_df = cache.get(cache_key)
            
            if not prices_df:
                # Add USDCAD for currency conversion
                symbols_with_usdcad = symbols + ['USDCAD=X']
                
                try:
                    prices_df = yf.download(' '.join(symbols_with_usdcad),
                                           start=start_date.strftime('%Y-%m-%d'),
                                           end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d'),
                                           auto_adjust=True, progress=False)['Close']
                    cache.set(cache_key, prices_df, 3600)  # Cache for 1 hour
                except Exception:
                    prices_df = pd.DataFrame()
            
            # Calculate market value for this date
            market_value = Decimal('0')
            
            for symbol in symbols:
                try:
                    # Get price for this symbol on this date
                    price_series = prices_df[symbol]
                    if not price_series.empty:
                        price = price_series.asof(date)
                        
                        # Handle currency conversion
                        if symbol.endswith('CAD'):
                            fx_rate = Decimal('1')
                        else:
                            fx_rate = prices_df['USDCAD=X'].asof(date)
                            
                        # Add to market value
                        market_value += price * holdings[symbol] * fx_rate
                except Exception:
                    # Skip symbols with price errors
                    pass
            
            results.append({'date': date.strftime('%Y-%m-%d'), 'market_value_cad': market_value})
        
        return results
    except Exception:
        return []


def compute_realised_pnl(account_id: str = None) -> list[dict]:
    """Compute realised P&L using FIFO and average-cost methods."""
    qs = Transaction.objects.select_related('account')
    if account_id:
        qs = qs.filter(account__account_id=account_id)

    transactions = qs.filter(type__in=['BUY', 'SELL']).order_by('date', 'id')

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


def fetch_last_prices(symbols: list[str], cache_timeout: int = 900) -> dict[str, Decimal]:
    """Fetch latest prices for given symbols using yfinance."""
    to_fetch = list(symbols)
    if 'USDCAD=X' not in to_fetch:
        to_fetch.append('USDCAD=X')

    cached_prices = {}
    uncached_symbols = []

    for sym in to_fetch:
        price = cache.get(f'price_{sym}')
        if price is not None:
            cached_prices[sym] = price
        else:
            uncached_symbols.append(sym)

    if uncached_symbols:
        try:
            tickers = yf.Tickers(' '.join(uncached_symbols))
            for sym in uncached_symbols:
                try:
                    ticker_obj = tickers.tickers[sym]
                    price = ticker_obj.fast_info.last_price
                    if price is not None:
                        price = Decimal(str(price))
                        cache.set(f'price_{sym}', price, cache_timeout)
                        cached_prices[sym] = price
                except Exception:
                    pass
        except Exception:
            pass

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