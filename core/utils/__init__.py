"""Core utilities package.

This package is a backward-compatible re-export module. All functions are
implemented in focused sub-modules but re-exported here so existing imports
(``from core.utils import ...``) continue to work without changes.

Modules:
    currencies: Cash-equivalent fund constants and transaction type tuples.
    dates: Date series generation utilities.
    prices: Price loading, caching, and fetching utilities.
    portfolio: Portfolio valuation, position management, and time-series calculations.
"""

# Re-export everything that was previously available from core.utils

# From currencies.py
from .currencies import (
    POSITION_TRANSACTION_TYPES,
    CASH_EQUIV_CAD,
    CASH_EQUIV_USD,
)

# From dates.py
from .dates import generate_date_series

# From prices.py
from .prices import (
    load_prices_from_db,
    load_prices_from_yfinance,
    fetch_last_prices,
    enrich_positions_with_prices,
)

# From portfolio.py
from .portfolio import (
    compute_portfolio_value_series,
    compute_realised_pnl,
    compute_cost_basis_series,
    compute_income_series,
    compute_series_by_account,
    compute_positions,
    group_positions_by_account,
    group_positions_by_category,
)

# Backward-compatibility aliases for functions that had leading underscores
# (some code imports _generate_date_series, _load_prices_from_db, etc.)
_generate_date_series = generate_date_series
_load_prices_from_db = load_prices_from_db
_load_prices_from_yfinance = load_prices_from_yfinance

__all__ = [
    # currencies
    'POSITION_TRANSACTION_TYPES',
    'CASH_EQUIV_CAD',
    'CASH_EQUIV_USD',
    # dates
    'generate_date_series',
    '_generate_date_series',
    # prices
    'load_prices_from_db',
    'load_prices_from_yfinance',
    'fetch_last_prices',
    'enrich_positions_with_prices',
    '_load_prices_from_db',
    '_load_prices_from_yfinance',
    # portfolio
    'compute_portfolio_value_series',
    'compute_realised_pnl',
    'compute_cost_basis_series',
    'compute_income_series',
    'compute_series_by_account',
    'compute_positions',
    'group_positions_by_account',
    'group_positions_by_category',
]

