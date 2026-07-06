"""Price loading, caching, and fetching utilities.

Provides functions for loading historical prices from the database or
yfinance, fetching latest prices, and enriching positions with market data.
"""

import logging
from decimal import Decimal

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def load_prices_from_db(symbols: list[str], start, end):
    """Load historical prices from the HistoricalPrice table into a pandas DataFrame.

    Returns a DataFrame with DatetimeIndex and symbols as columns, or None if insufficient data.
    """
    try:
        from core.models import HistoricalPrice  # avoid circular import at module level

        qs = (
            HistoricalPrice.objects.filter(
                symbol__in=symbols,
                date__range=(start, end),
            )
            .values_list("symbol", "date", "close")
            .order_by("date")
        )

        rows = list(qs)
        if not rows:
            return None

        # Check that at least the portfolio symbols (not just USDCAD) have data
        db_symbols = {r[0] for r in rows}
        non_fx_needed = [s for s in symbols if s != "USDCAD=X"]
        if not any(s in db_symbols for s in non_fx_needed):
            return None

        prices_dict: dict[str, dict] = {}
        for symbol, price_date, close in rows:
            if symbol not in prices_dict:
                prices_dict[symbol] = {}
            prices_dict[symbol][price_date] = float(close)

        df = pd.DataFrame(prices_dict)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        return df
    except Exception as exc:
        logger.warning(f"load_prices_from_db error: {exc}")
        return None


def load_prices_from_yfinance(symbols: list[str], start, end):
    """Download closing prices from yfinance for the given symbols and date range.

    Symbols are resolved to yfinance-compatible tickers (e.g., VDY → VDY.TO)
    before fetching, then columns are mapped back to original symbol names.

    Returns a DataFrame with DatetimeIndex and symbols as columns, or None on failure.
    """
    try:
        # Resolve symbols for yfinance
        resolved_map = {}  # original → resolved
        resolved_symbols = []
        for sym in symbols:
            try:
                from risk.models import SecurityUniverse

                su = SecurityUniverse.objects.filter(symbol=sym).first()
                primary_currency = su.primary_currency if su else None
            except Exception:
                primary_currency = None
            resolved = resolve_yahoo_symbol(sym, primary_currency)
            resolved_map[resolved] = sym
            resolved_symbols.append(resolved)

        cache_key = f"hist_prices_{','.join(sorted(symbols))}_{start}_{end}"
        df = cache.get(cache_key)
        if df is not None and not df.empty:
            logger.info("Using cached yfinance prices")
            return df

        logger.info(
            f"Fetching {len(resolved_symbols)} symbols from yfinance: {start} → {end}"
        )
        raw_df = yf.download(
            resolved_symbols,
            start=start.strftime("%Y-%m-%d"),
            end=(end + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )
        if raw_df is None or raw_df.empty:
            return None

        # Extract Close prices
        if isinstance(raw_df.columns, pd.MultiIndex):
            lvl0 = raw_df.columns.get_level_values(0)
            if "Close" in lvl0:
                df = raw_df["Close"]
            else:
                df = raw_df.xs("Close", axis=1, level=1)
        elif len(resolved_symbols) == 1:
            df = raw_df[["Close"]].rename(columns={"Close": resolved_symbols[0]})
        else:
            df = raw_df.get("Close", raw_df)

        # Map resolved columns back to original symbols
        col_map = {k: v for k, v in resolved_map.items() if k in df.columns}
        if col_map:
            df = df.rename(columns=col_map)

        cache.set(cache_key, df, settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
        return df
    except Exception as exc:
        logger.error(f"load_prices_from_yfinance error: {type(exc).__name__}: {exc}")
        return None


def _get_current_price(ticker_obj, symbol: str):
    """Get the current/latest price for a ticker using multiple fallback methods.

    yfinance 1.3.0 has a bug where fast_info.last_price crashes with
    AttributeError: 'PriceHistory' object has no attribute '_dividends'.
    This function tries multiple approaches to get a price.
    """
    # Method 1: fast_info.last_price (fast but can crash)
    try:
        price = ticker_obj.fast_info.last_price
        if price is not None:
            return float(price)
    except Exception:
        pass

    # Method 2: info['regularMarketPrice']
    try:
        info = ticker_obj.info
        if info:
            price = (
                info.get("regularMarketPrice")
                or info.get("currentPrice")
                or info.get("previousClose")
            )
            if price is not None:
                return float(price)
    except Exception:
        pass

    # Method 3: .history(period='1d') — most reliable but slower
    try:
        hist = ticker_obj.history(period="1d")
        if not hist.empty and "Close" in hist.columns:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass

    return None


def resolve_yahoo_symbol(symbol: str, primary_currency: str | None = None) -> str:
    """Resolve a database symbol to its yfinance-compatible ticker.

    Rules:
    - If primary_currency is 'CAD' and symbol doesn't already have a suffix, append '.TO'
    - If primary_currency is 'USD', return symbol as-is
    - If symbol already has a suffix (contains '.', or is 'USDCAD=X'), return as-is
    - If primary_currency is None and symbol starts with '$', return as-is (crypto)
    - Default: return symbol as-is (assume USD)

    Examples:
        resolve_yahoo_symbol('VDY', 'CAD') → 'VDY.TO'
        resolve_yahoo_symbol('VDY.TO', 'CAD') → 'VDY.TO' (no change)
        resolve_yahoo_symbol('AAPL', 'USD') → 'AAPL'
        resolve_yahoo_symbol('USDCAD=X', 'CAD') → 'USDCAD=X' (no change)
    """
    # Never modify FX or already-suffixed symbols
    if symbol == "USDCAD=X" or "." in symbol or symbol.startswith("$"):
        return symbol

    # If primary_currency is CAD and no suffix, add .TO
    if primary_currency and primary_currency.upper() == "CAD":
        return f"{symbol}.TO"

    # Default: return as-is (assume USD/international)
    return symbol


def get_fixed_price(symbol: str) -> Decimal | None:
    """Look up a fixed price from FixedPriceConfig model.

    Returns the active_price (in configured currency) if the symbol exists
    and is active, otherwise returns None.

    Examples:
        get_fixed_price('RBF2010') → Decimal('10.000000')
        get_fixed_price('AAPL') → None
    """
    try:
        from core.models import FixedPriceConfig

        config = FixedPriceConfig.objects.filter(
            symbol=symbol,
            is_active=True,
        ).first()
        if config:
            return config.active_price
    except Exception:
        pass
    return None


def fetch_last_prices(symbols: list[str], cache_timeout=None) -> dict[str, Decimal]:
    """Fetch latest prices for given symbols using yfinance.

    Args:
        symbols: List of ticker symbols to fetch prices for.
        cache_timeout: Optional override for cache timeout (seconds).

    Returns:
        Dict mapping symbol to Decimal price. Always includes USDCAD=X.
    """
    if cache_timeout is None:
        cache_timeout = settings.PRICE_CACHE_TIMEOUT
    logger.info(f"fetch_last_prices called with {len(symbols)} symbols: {symbols}")

    to_fetch = list(symbols)
    if "USDCAD=X" not in to_fetch:
        to_fetch.append("USDCAD=X")

    cached_prices = {}
    uncached_symbols = []

    for sym in to_fetch:
        price = cache.get(f"price_{sym}")
        if price is not None:
            logger.debug(f"Using cached price for {sym}: {price}")
            cached_prices[sym] = price
        else:
            uncached_symbols.append(sym)

    if uncached_symbols:
        logger.info(
            f"Fetching prices from yfinance for {len(uncached_symbols)} symbols: {uncached_symbols}"
        )
        for sym in uncached_symbols:
            try:
                # Check if this is a fixed-price security
                fixed = get_fixed_price(sym)
                if fixed is not None:
                    cache.set(f"price_{sym}", fixed, cache_timeout)
                    cached_prices[sym] = fixed
                    logger.info(f"OK Fixed price for {sym}: {fixed}")
                    continue

                # Resolve symbol for yfinance (e.g., VDY → VDY.TO)
                # We need primary_currency from SecurityUniverse
                try:
                    from risk.models import SecurityUniverse

                    su = SecurityUniverse.objects.filter(symbol=sym).first()
                    primary_currency = su.primary_currency if su else None
                except Exception:
                    primary_currency = None

                yahoo_sym = resolve_yahoo_symbol(sym, primary_currency)

                ticker_obj = yf.Ticker(yahoo_sym)
                price = _get_current_price(ticker_obj, yahoo_sym)
                if price is not None:
                    price = Decimal(str(price))
                    cache.set(
                        f"price_{sym}", price, cache_timeout
                    )  # cache by ORIGINAL symbol
                    cached_prices[sym] = price
                    logger.info(
                        f"OK Fetched price for {sym} (resolved: {yahoo_sym}): {price}"
                    )
                else:
                    logger.warning(f"✗ No price available for {sym}")
            except Exception as e:
                logger.error(
                    f"✗ Error fetching price for {sym}: {type(e).__name__}: {e}"
                )
    else:
        logger.info("All prices served from cache")

    logger.info(f"Returning {len(cached_prices)} prices: {list(cached_prices.keys())}")
    return cached_prices


def enrich_positions_with_prices(positions: list[dict], prices: dict) -> list[dict]:
    """Enrich positions with market prices and calculate P&L.

    Args:
        positions: List of position dicts with 'total_cost_cad' and 'total_shares'.
        prices: Dict mapping symbol (and 'USDCAD=X') to Decimal price.

    Returns:
        Enriched position dicts with 'last_price', 'market_value_cad',
        'unrealised_pnl_cad', and 'unrealised_pnl_pct' added.
    """
    usdcad = prices.get("USDCAD=X", Decimal("1"))

    enriched = []
    for pos in positions:
        last_price = prices.get(pos["symbol"])

        fx = usdcad if pos["currency"] != "CAD" else Decimal("1")

        market_value_cad = None
        if last_price is not None:
            market_value_cad = last_price * pos["total_shares"] * fx

        unrealised_pnl_cad = None
        if market_value_cad is not None:
            unrealised_pnl_cad = market_value_cad - pos["total_cost_cad"]

        unrealised_pnl_pct = None
        if unrealised_pnl_cad is not None and pos["total_cost_cad"] != 0:
            unrealised_pnl_pct = unrealised_pnl_cad / pos["total_cost_cad"] * 100

        enriched.append(
            {
                **pos,
                "last_price": last_price,
                "market_value_cad": market_value_cad,
                "unrealised_pnl_cad": unrealised_pnl_cad,
                "unrealised_pnl_pct": unrealised_pnl_pct,
            }
        )

    return enriched
