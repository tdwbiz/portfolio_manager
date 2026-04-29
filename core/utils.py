from decimal import Decimal
import yfinance as yf
from django.core.cache import cache
from core.models import Transaction

def compute_positions(broker=None):
    """
    Compute positions from Transaction queryset.
    Returns a list of dicts with keys: symbol, broker, account_name, currency, total_shares, avg_cost_cad, total_cost_cad.
    """
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
            
    return result


def fetch_last_prices(symbols: list[str], cache_timeout: int = 900) -> dict[str, Decimal]:
    """
    Fetch latest prices for given symbols using yfinance.
    Returns a dict {symbol: Decimal(price)}.
    """
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
    """
    Enrich positions with market prices and calculate P&L.
    """
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
