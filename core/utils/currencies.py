"""Currency-related constants for portfolio calculations.

This module provides constants for cash-equivalent mutual funds and
transaction type classification.
"""

from decimal import Decimal

POSITION_TRANSACTION_TYPES = ('BUY', 'SELL')

# Cash-equivalent mutual funds: always $10 per share, no exchange price needed.
# RBF2010 = RBC CAD money market fund (CAD-denominated).
# RBF2014 = RBC USD money market fund (USD-denominated).
# Add any additional fixed-price fund codes here as needed.
CASH_EQUIV_CAD = {'RBF2010': Decimal('10.00')}
CASH_EQUIV_USD = {'RBF2014': Decimal('10.00')}
