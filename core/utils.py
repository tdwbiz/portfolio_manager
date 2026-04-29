from decimal import Decimal
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
