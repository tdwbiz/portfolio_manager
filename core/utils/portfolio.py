"""Portfolio valuation, position management, and time-series calculations.

Provides functions for:
- Computing portfolio value series with historical prices
- Computing realised P&L using FIFO and average-cost methods
- Computing cost basis series over time
- Computing income (dividends, distributions, interest) series
- Computing per-account value series
- Computing and grouping positions
"""

import logging
import math
from collections import deque
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.models import Transaction

from .currencies import CASH_EQUIV_CAD, CASH_EQUIV_USD
from .dates import generate_date_series
from .prices import load_prices_from_db, load_prices_from_yfinance

logger = logging.getLogger(__name__)


def compute_portfolio_value_series(
    freq: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute portfolio market value over time with historical prices.

    Prices are sourced from the HistoricalPrice table (populated by the
    fetch_historical_prices management command) with live yfinance as fallback.

    Args:
        freq: Frequency of data points ('daily', 'weekly', 'monthly', 'annual').
        date_from: Optional ISO date string to clip the start of the series.
        date_to: Optional ISO date string to clip the end of the series.

    Returns:
        List of dicts with 'date' (ISO string) and 'market_value_cad' (Decimal).
    """
    logger.info("compute_portfolio_value_series called")
    try:
        # Get all transactions ordered by date
        transactions = Transaction.objects.filter(type__in=("BUY", "SELL")).order_by(
            "date", "id"
        )

        if not transactions.exists():
            logger.info("No BUY/SELL transactions found, returning empty series")
            return []

        # Always use the FULL transaction history for holdings, but clip the visible date range.
        # Price lookup covers the full history (from first transaction) so asof() always works.
        first_tx_date = transactions.first().date
        end_date = timezone.now().date()

        # Determine the visible clip window (what dates show on the chart)
        clip_start = first_tx_date
        if date_from:
            try:
                clip_start = max(
                    first_tx_date, datetime.strptime(date_from, "%Y-%m-%d").date()
                )
            except ValueError:
                pass
        if date_to:
            try:
                end_date = min(end_date, datetime.strptime(date_to, "%Y-%m-%d").date())
            except ValueError:
                pass

        logger.info(f"Visible date range: {clip_start} to {end_date}")

        # Generate period-end dates based on freq
        dates = generate_date_series(clip_start, end_date, freq)

        # Collect all unique symbols ever held
        all_symbols = list(set(tx.symbol for tx in transactions))
        logger.info(f"Found {len(all_symbols)} unique symbols: {all_symbols}")
        symbols_with_usdcad = list(set(all_symbols + ["USDCAD=X"]))

        # ---- Build prices DataFrame ----
        # 1. Try HistoricalPrice DB (from fetch_historical_prices command)
        # 2. Fall back to live yfinance
        prices_df = load_prices_from_db(symbols_with_usdcad, first_tx_date, end_date)

        if prices_df is None or prices_df.empty:
            logger.info(
                "HistoricalPrice DB empty/incomplete — falling back to live yfinance"
            )
            prices_df = load_prices_from_yfinance(
                symbols_with_usdcad, first_tx_date, end_date
            )
        else:
            logger.info(
                f"Using DB prices: {len(prices_df)} rows × {len(prices_df.columns)} symbols"
            )

        # Calculate market value for each date point
        results = []

        for tx_date in dates:
            holdings = {}

            # Replay transactions up to this date
            for tx in transactions.filter(date__lte=tx_date):
                symbol = tx.symbol
                if tx.type == "BUY":
                    holdings[symbol] = holdings.get(symbol, Decimal("0")) + tx.quantity
                elif tx.type == "SELL":
                    holdings[symbol] = holdings.get(symbol, Decimal("0")) - tx.quantity

            # Get unique symbols with positive holdings
            symbols = [s for s, qty in holdings.items() if qty > 0]

            # Calculate market value for this date
            market_value = Decimal("0")

            if prices_df is not None and not prices_df.empty:
                for symbol in symbols:
                    try:
                        # Cash-equivalent funds: fixed $10, no DB lookup needed
                        if symbol in CASH_EQUIV_CAD:
                            price = CASH_EQUIV_CAD[symbol]
                            market_value += price * holdings[symbol]
                            continue
                        if symbol in CASH_EQUIV_USD:
                            price = CASH_EQUIV_USD[symbol]
                            if "USDCAD=X" in prices_df.columns:
                                raw_fx = (
                                    prices_df["USDCAD=X"]
                                    .dropna()
                                    .asof(pd.Timestamp(tx_date))
                                )
                                fx_rate = (
                                    Decimal(str(raw_fx))
                                    if (
                                        raw_fx is not None
                                        and not (
                                            isinstance(raw_fx, float)
                                            and math.isnan(raw_fx)
                                        )
                                    )
                                    else Decimal("1")
                                )
                            else:
                                fx_rate = Decimal("1")
                            market_value += price * holdings[symbol] * fx_rate
                            continue

                        if symbol not in prices_df.columns:
                            logger.warning(
                                f"Symbol {symbol!r} not in prices_df columns; skipping."
                            )
                            continue
                        price_series = prices_df[symbol].dropna()
                        if price_series.empty:
                            logger.warning(
                                f"No price data for {symbol!r} on {tx_date}; skipping."
                            )
                            continue
                        raw_price = price_series.asof(pd.Timestamp(tx_date))
                        if raw_price is None or (
                            isinstance(raw_price, float) and math.isnan(raw_price)
                        ):
                            logger.warning(
                                f"NaN/None price for {symbol!r} on {tx_date}; skipping."
                            )
                            continue
                        price = Decimal(str(raw_price))

                        if (
                            symbol.endswith(".TO")
                            or symbol.endswith(".AX")
                            or symbol.endswith(".V")
                        ):
                            # Already CAD-denominated TSX/ASX/TSXV stock
                            fx_rate = Decimal("1")
                        elif "USDCAD=X" in prices_df.columns:
                            raw_fx = (
                                prices_df["USDCAD=X"]
                                .dropna()
                                .asof(pd.Timestamp(tx_date))
                            )
                            if raw_fx is None or (
                                isinstance(raw_fx, float) and math.isnan(raw_fx)
                            ):
                                logger.warning(
                                    f"NaN FX rate on {tx_date}; using 1.0 fallback."
                                )
                                fx_rate = Decimal("1")
                            else:
                                fx_rate = Decimal(str(raw_fx))
                        else:
                            logger.warning("USDCAD=X not in prices_df; using fx_rate=1")
                            fx_rate = Decimal("1")

                        market_value += price * holdings[symbol] * fx_rate
                    except Exception as sym_err:
                        logger.warning(
                            f"Price lookup error for {symbol!r} on {tx_date}: {sym_err}"
                        )

            results.append(
                {
                    "date": tx_date.strftime("%Y-%m-%d"),
                    "market_value_cad": market_value,
                }
            )

        if prices_df is not None and not prices_df.empty and not results:
            logger.warning(
                "Price fetch succeeded but results are empty — check date range or symbols."
            )

        return results
    except Exception:
        logger.exception(
            "Error in compute_portfolio_value_series — returned empty series"
        )
        return []


def compute_realised_pnl(
    account_id: str | None = None,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute realised P&L using FIFO and average-cost methods.

    Args:
        account_id: Optional account filter.
        symbol: Optional symbol filter (case-insensitive).
        date_from: Optional ISO date string to filter results (sell_date >= date_from).
                   Applied AFTER FIFO/avg-cost lot processing.
        date_to: Optional ISO date string to filter results (sell_date <= date_to).
                 Applied AFTER FIFO/avg-cost lot processing.

    Returns:
        List of dicts with P&L data per closed position.
    """
    qs = Transaction.objects.select_related("account")
    if account_id:
        qs = qs.filter(account__account_id=account_id)

    # Symbol filter on the query (case-insensitive)
    if symbol:
        qs = qs.filter(symbol__iexact=symbol)

    transactions = qs.filter(type__in=("BUY", "SELL")).order_by("date", "id")

    # Group by (symbol, account_id), preserving chronological order
    groups: dict[tuple, list] = {}
    for tx in transactions:
        key = (tx.symbol, tx.account.account_id if tx.account else "")
        groups.setdefault(key, []).append(tx)

    result = []

    for (symbol_key, acct_id), txs in groups.items():
        fifo_deque: deque = deque()
        total_shares = Decimal("0")
        total_cost = Decimal("0")

        for tx in txs:
            cost = abs(tx.amount_cad)

            if tx.type == "BUY":
                fifo_deque.append({"qty": tx.quantity, "cost": cost})
                total_shares += tx.quantity
                total_cost += cost

            elif tx.type == "SELL":
                sell_amount = abs(tx.amount_cad)
                qty_to_sell = tx.quantity

                # FIFO cost basis
                qty_remaining = qty_to_sell
                matched_cost_fifo = Decimal("0")
                while qty_remaining > 0 and fifo_deque:
                    lot = fifo_deque[0]
                    if lot["qty"] <= qty_remaining:
                        matched_cost_fifo += lot["cost"]
                        qty_remaining -= lot["qty"]
                        fifo_deque.popleft()
                    else:
                        fraction = qty_remaining / lot["qty"]
                        cost_taken = lot["cost"] * fraction
                        matched_cost_fifo += cost_taken
                        lot["cost"] -= cost_taken
                        lot["qty"] -= qty_remaining
                        qty_remaining = Decimal("0")

                # Avg-cost basis
                avg_cost_per_share = (
                    total_cost / total_shares if total_shares > 0 else Decimal("0")
                )
                cost_basis_avg = qty_to_sell * avg_cost_per_share

                # Update running avg-cost state
                total_shares -= qty_to_sell
                total_cost -= cost_basis_avg

                result.append(
                    {
                        "symbol": symbol_key,
                        "account_id": acct_id,
                        "sell_date": tx.date,
                        "qty_sold": qty_to_sell,
                        "sell_amount_cad": sell_amount,
                        "cost_basis_fifo": matched_cost_fifo,
                        "cost_basis_avg": cost_basis_avg,
                        "realised_pnl_fifo": sell_amount - matched_cost_fifo,
                        "realised_pnl_avg": sell_amount - cost_basis_avg,
                    }
                )

    # Apply date range filter AFTER all FIFO/avg-cost processing
    if date_from or date_to:
        filtered = []
        for trade in result:
            sell_date = trade["sell_date"]
            if date_from:
                try:
                    df = datetime.strptime(date_from, "%Y-%m-%d").date()
                    if sell_date < df:
                        continue
                except ValueError:
                    pass
            if date_to:
                try:
                    dt = datetime.strptime(date_to, "%Y-%m-%d").date()
                    if sell_date > dt:
                        continue
                except ValueError:
                    pass
            filtered.append(trade)
        result = filtered

    result.sort(key=lambda x: x["sell_date"], reverse=True)
    return result


def compute_cost_basis_series(
    date_from: str | None = None,
    date_to: str | None = None,
    freq: str = "monthly",
) -> list[dict]:
    """Compute total invested cost basis over time.

    For each period-end date, sum the amount_cad of all BUY transactions
    (absolute value) up to and including that date, minus the cost of all
    sold shares (approximated as average cost at time of sale).

    Uses an incremental pointer over a pre-fetched transaction list to avoid
    re-querying per date (fixes double-count bug).

    Returns a list of dicts: [{'date': 'YYYY-MM-DD', 'cost_basis_cad': Decimal}]
    """
    try:
        transactions = Transaction.objects.filter(type__in=("BUY", "SELL")).order_by(
            "date", "id"
        )

        if not transactions.exists():
            return []

        # Determine date range
        start_date = transactions.first().date
        end_date = timezone.now().date()

        if date_from:
            try:
                parsed_from = datetime.strptime(date_from, "%Y-%m-%d").date()
                start_date = max(start_date, parsed_from)
            except ValueError:
                pass

        if date_to:
            try:
                parsed_to = datetime.strptime(date_to, "%Y-%m-%d").date()
                end_date = min(end_date, parsed_to)
            except ValueError:
                pass

        dates = generate_date_series(start_date, end_date, freq)

        # Use a pointer into the transactions list to avoid re-querying
        all_txs = list(transactions)
        tx_idx = 0

        # Incremental state keyed by (symbol, account_id)
        total_shares: dict[str, Decimal] = {}
        total_cost: dict[str, Decimal] = {}

        results = []
        for tx_date in dates:
            # Advance pointer: process only NEW transactions up to tx_date
            while tx_idx < len(all_txs) and all_txs[tx_idx].date <= tx_date:
                tx = all_txs[tx_idx]
                key = (tx.symbol, tx.account_id if tx.account_id else "")
                cost = abs(tx.amount_cad)

                if tx.type == "BUY":
                    total_shares[key] = (
                        total_shares.get(key, Decimal("0")) + tx.quantity
                    )
                    total_cost[key] = total_cost.get(key, Decimal("0")) + cost
                elif tx.type == "SELL":
                    shares = total_shares.get(key, Decimal("0"))
                    avg = total_cost[key] / shares if shares > 0 else Decimal("0")
                    sold_cost = tx.quantity * avg
                    total_shares[key] = max(shares - tx.quantity, Decimal("0"))
                    total_cost[key] = max(
                        total_cost.get(key, Decimal("0")) - sold_cost, Decimal("0")
                    )
                tx_idx += 1

            running_cost = sum(total_cost.values())
            results.append(
                {
                    "date": tx_date.strftime("%Y-%m-%d"),
                    "cost_basis_cad": running_cost,
                }
            )

        return results
    except Exception:
        logger.exception("Error in compute_cost_basis_series — returned empty series")
        return []


def compute_income_series(
    date_from: str | None = None,
    date_to: str | None = None,
    freq: str = "monthly",
) -> list[dict]:
    """Compute income (dividends, distributions, interest) over time.

    For each period-end date, sums amount_cad of transactions with type in
    ('DIV', 'DIST', 'INT') that fall within that period.

    Returns a list of dicts: [{'date': 'YYYY-MM-DD', 'income_cad': Decimal}]
    """
    cache_key = "income_series"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        income_types = ("DIV", "DIST", "INT")
        transactions = Transaction.objects.filter(type__in=income_types).order_by(
            "date", "id"
        )

        if not transactions.exists():
            cache.set(cache_key, [], settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
            return []

        # Determine date range
        start_date = transactions.first().date
        end_date = timezone.now().date()

        if date_from:
            try:
                parsed_from = datetime.strptime(date_from, "%Y-%m-%d").date()
                if parsed_from < start_date:
                    start_date = parsed_from
                elif parsed_from > end_date:
                    start_date = end_date
            except ValueError:
                pass

        if date_to:
            try:
                parsed_to = datetime.strptime(date_to, "%Y-%m-%d").date()
                if parsed_to > end_date:
                    end_date = parsed_to
                elif parsed_to < start_date:
                    end_date = start_date
            except ValueError:
                pass

        dates = generate_date_series(start_date, end_date, freq)

        # Build a lookup of income by period-end date
        income_by_date = {}
        for tx in transactions:
            # Find the period-end date that this transaction falls into
            if freq == "monthly":
                tx_period_end = date(tx.date.year, tx.date.month, 1)
                if tx_period_end.month == 12:
                    tx_period_end = tx_period_end.replace(
                        year=tx_period_end.year + 1, month=1, day=1
                    )
                else:
                    tx_period_end = tx_period_end.replace(
                        month=tx_period_end.month + 1, day=1
                    )
                tx_period_end = tx_period_end - timedelta(days=1)
                income_by_date[tx_period_end] = (
                    income_by_date.get(tx_period_end, Decimal("0")) + tx.amount_cad
                )
            elif freq == "weekly":
                # Find the Friday on or after the transaction date
                days_to_friday = (4 - tx.date.weekday()) % 7
                tx_period_end = tx.date + timedelta(days=days_to_friday)
                income_by_date[tx_period_end] = (
                    income_by_date.get(tx_period_end, Decimal("0")) + tx.amount_cad
                )
            elif freq == "annual":
                tx_period_end = date(tx.date.year, 12, 31)
                income_by_date[tx_period_end] = (
                    income_by_date.get(tx_period_end, Decimal("0")) + tx.amount_cad
                )
            else:
                # daily: each transaction goes to its own date
                income_by_date[tx.date] = (
                    income_by_date.get(tx.date, Decimal("0")) + tx.amount_cad
                )

        results = []
        for tx_date in dates:
            results.append(
                {
                    "date": tx_date.strftime("%Y-%m-%d"),
                    "income_cad": income_by_date.get(tx_date, Decimal("0")),
                }
            )

        cache.set(cache_key, results, settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
        return results
    except Exception:
        logger.exception("Error in compute_income_series — returned empty series")
        return []


def compute_series_by_account(
    date_from: str | None = None,
    date_to: str | None = None,
    freq: str = "monthly",
) -> dict[str, list[dict]]:
    """Compute market value series for each account independently.

    All accounts share the same global date grid (from the earliest
    transaction across all accounts to end_date), ensuring arrays
    are the same length as the labels produced by the market-value series.

    Returns a dict keyed by account_id, each value being a list of
    {'date': 'YYYY-MM-DD', 'market_value_cad': Decimal} dicts.
    """
    try:
        # Get all accounts that have at least one BUY transaction
        accounts_with_buys = (
            Transaction.objects.filter(type="BUY")
            .values_list("account__account_id", flat=True)
            .distinct()
        )

        if not accounts_with_buys:
            return {}

        # Compute global date range from ALL accounts
        global_start = (
            Transaction.objects.filter(
                account__account_id__in=accounts_with_buys, type__in=("BUY", "SELL")
            )
            .order_by("date", "id")
            .first()
        )

        if not global_start:
            return {}

        start_date = global_start.date
        end_date = timezone.now().date()

        if date_from:
            try:
                parsed_from = datetime.strptime(date_from, "%Y-%m-%d").date()
                start_date = max(start_date, parsed_from)
            except ValueError:
                pass

        if date_to:
            try:
                parsed_to = datetime.strptime(date_to, "%Y-%m-%d").date()
                end_date = min(end_date, parsed_to)
            except ValueError:
                pass

        # Generate a single shared dates list
        dates = generate_date_series(start_date, end_date, freq)

        # Fetch prices once using ALL symbols across ALL accounts
        cache_key = "hist_prices"
        prices_df = cache.get(cache_key)

        if prices_df is None or (hasattr(prices_df, "empty") and prices_df.empty):
            all_symbols = list(
                set(
                    tx.symbol
                    for tx in Transaction.objects.filter(
                        account__account_id__in=accounts_with_buys
                    ).values_list("symbol", flat=True)
                )
            )
            symbols_with_usdcad = list(set(all_symbols + ["USDCAD=X"]))

            try:
                raw_df = yf.download(
                    " ".join(symbols_with_usdcad),
                    start=start_date.strftime("%Y-%m-%d"),
                    end=(end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                    auto_adjust=True,
                    progress=False,
                    group_by="ticker",
                )
                if isinstance(raw_df.columns, pd.MultiIndex):
                    prices_df = (
                        raw_df["Close"]
                        if "Close" in raw_df.columns.get_level_values(0)
                        else raw_df.xs("Close", axis=1, level=0)
                    )
                elif len(symbols_with_usdcad) == 1:
                    prices_df = raw_df[["Close"]].rename(
                        columns={"Close": symbols_with_usdcad[0]}
                    )
                else:
                    prices_df = raw_df.get("Close", raw_df)
                cache.set(cache_key, prices_df, settings.HISTORICAL_PRICE_CACHE_TIMEOUT)
            except Exception as e:
                logger.error(f"Error fetching historical data for by-account: {e}")
                prices_df = None

        results = {}
        for account_id in accounts_with_buys:
            account_txs = Transaction.objects.filter(
                account__account_id=account_id, type__in=("BUY", "SELL")
            ).order_by("date", "id")

            if not account_txs.exists():
                continue

            # Use the same dates list
            account_results = []
            for tx_date in dates:
                holdings = {}

                for tx in account_txs.filter(date__lte=tx_date):
                    symbol = tx.symbol
                    if tx.type == "BUY":
                        holdings[symbol] = (
                            holdings.get(symbol, Decimal("0")) + tx.quantity
                        )
                    elif tx.type == "SELL":
                        holdings[symbol] = (
                            holdings.get(symbol, Decimal("0")) - tx.quantity
                        )

                symbols = [s for s, qty in holdings.items() if qty > 0]
                market_value = Decimal("0")

                if prices_df is not None and not prices_df.empty:
                    for symbol in symbols:
                        try:
                            # Cash-equivalent funds: fixed $10, no DB lookup needed
                            if symbol in CASH_EQUIV_CAD:
                                market_value += (
                                    CASH_EQUIV_CAD[symbol] * holdings[symbol]
                                )
                                continue
                            if symbol in CASH_EQUIV_USD:
                                price = CASH_EQUIV_USD[symbol]
                                if "USDCAD=X" in prices_df.columns:
                                    raw_fx = (
                                        prices_df["USDCAD=X"]
                                        .dropna()
                                        .asof(pd.Timestamp(tx_date))
                                    )
                                    fx_rate = (
                                        Decimal(str(raw_fx))
                                        if (
                                            raw_fx is not None
                                            and not (
                                                isinstance(raw_fx, float)
                                                and math.isnan(raw_fx)
                                            )
                                        )
                                        else Decimal("1")
                                    )
                                else:
                                    fx_rate = Decimal("1")
                                market_value += price * holdings[symbol] * fx_rate
                                continue

                            if symbol not in prices_df.columns:
                                continue
                            price_series = prices_df[symbol].dropna()
                            if price_series.empty:
                                continue
                            raw_price = price_series.asof(pd.Timestamp(tx_date))
                            if raw_price is None or (
                                isinstance(raw_price, float) and math.isnan(raw_price)
                            ):
                                continue
                            price = Decimal(str(raw_price))

                            if (
                                symbol.endswith(".TO")
                                or symbol.endswith(".AX")
                                or symbol.endswith(".V")
                            ):
                                fx_rate = Decimal("1")
                            elif "USDCAD=X" in prices_df.columns:
                                raw_fx = (
                                    prices_df["USDCAD=X"]
                                    .dropna()
                                    .asof(pd.Timestamp(tx_date))
                                )
                                if raw_fx is None or (
                                    isinstance(raw_fx, float) and math.isnan(raw_fx)
                                ):
                                    fx_rate = Decimal("1")
                                else:
                                    fx_rate = Decimal(str(raw_fx))
                            else:
                                fx_rate = Decimal("1")

                            market_value += price * holdings[symbol] * fx_rate
                        except Exception:
                            logger.debug(
                                "Skipping symbol %s on %s for account %s — price lookup failed",
                                symbol,
                                tx_date,
                                account_id,
                            )

                account_results.append(
                    {
                        "date": tx_date.strftime("%Y-%m-%d"),
                        "market_value_cad": market_value,
                    }
                )

            results[str(account_id)] = account_results

        return results
    except Exception:
        logger.exception("Error in compute_series_by_account — returned empty series")
        return {}


def compute_positions(broker=None):
    """Compute positions from Transaction queryset.

    Args:
        broker: Optional statement source filter.

    Returns:
        List of position dicts with shares, cost, and metadata.
    """
    qs = Transaction.objects.select_related("statement", "account").all()
    if broker:
        qs = qs.filter(statement__source=broker)

    # Group by (symbol, broker, account)
    positions = {}

    for tx in qs:
        account_id = tx.account.account_id if tx.account else ""
        account_name = tx.account.name if tx.account else ""
        key = (tx.symbol, tx.statement.source, account_id)
        if key not in positions:
            positions[key] = {
                "symbol": tx.symbol,
                "broker": tx.statement.source,
                "account_id": account_id,
                "account_name": account_name,
                "category": tx.account.category if tx.account else "TAXABLE",
                "category_display": tx.account.get_category_display()
                if tx.account
                else "Taxable",
                "owner": tx.account.owner if tx.account else "",
                "currency": tx.currency,
                "total_shares": Decimal("0"),
                "total_cost_cad": Decimal("0"),
                "buy_qty": Decimal("0"),
                "buy_cost_cad": Decimal("0"),
            }

        pos = positions[key]

        if tx.type == "BUY":
            pos["total_shares"] += tx.quantity
            pos["total_cost_cad"] += tx.amount_cad
            pos["buy_qty"] += tx.quantity
            pos["buy_cost_cad"] += tx.amount_cad
        elif tx.type == "SELL":
            pos["total_shares"] -= tx.quantity
            pos["total_cost_cad"] -= tx.amount_cad

    # Convert to list of dicts
    result = []
    for key, pos in positions.items():
        if pos["total_shares"] > 0:
            avg_cost = (
                pos["buy_cost_cad"] / pos["buy_qty"] if pos["buy_qty"] else Decimal("0")
            )
            result.append(
                {
                    "symbol": pos["symbol"],
                    "broker": pos["broker"],
                    "account_id": pos["account_id"],
                    "account_name": pos["account_name"],
                    "category": pos["category"],
                    "category_display": pos["category_display"],
                    "owner": pos["owner"],
                    "currency": pos["currency"],
                    "total_shares": pos["total_shares"],
                    "avg_cost_cad": avg_cost,
                    "total_cost_cad": pos["total_cost_cad"],
                }
            )

    # Sort by broker, account name, symbol
    result.sort(key=lambda x: (x["broker"], x["account_name"], x["symbol"] or ""))
    return result


def group_positions_by_account(positions: list[dict]) -> list[dict]:
    """Group an enriched positions list by account, computing per-account subtotals.

    Returns a list of account-group dicts, each with:
        account_id, account_name, broker, category, category_display, owner,
        positions (list of position dicts for that account),
        subtotal_cost_cad, subtotal_market_value_cad, subtotal_unrealised_pnl_cad
    """
    groups: dict[str, dict] = {}

    for pos in positions:
        acct_id = pos["account_id"]
        if acct_id not in groups:
            groups[acct_id] = {
                "account_id": acct_id,
                "account_name": pos["account_name"],
                "broker": pos["broker"],
                "category": pos["category"],
                "category_display": pos["category_display"],
                "owner": pos["owner"],
                "positions": [],
                "subtotal_cost_cad": Decimal("0"),
                "subtotal_market_value_cad": Decimal("0"),
                "subtotal_unrealised_pnl_cad": Decimal("0"),
            }

        group = groups[acct_id]
        group["positions"].append(pos)
        group["subtotal_cost_cad"] += pos["total_cost_cad"]
        if pos.get("market_value_cad") is not None:
            group["subtotal_market_value_cad"] += pos["market_value_cad"]

    # Compute unrealised P&L for each group
    for group in groups.values():
        group["subtotal_unrealised_pnl_cad"] = (
            group["subtotal_market_value_cad"] - group["subtotal_cost_cad"]
        )

    # Sort by (category, broker, account_id)
    category_order = {
        "RRSP": 0,
        "LIRA": 1,
        "TFSA": 2,
        "RESP": 3,
        "TAXABLE": 4,
        "OTHER": 5,
    }

    result = list(groups.values())
    result.sort(
        key=lambda g: (
            category_order.get(g["category"], 99),
            g["broker"],
            g["account_id"],
        )
    )

    return result


def group_positions_by_category(positions: list[dict]) -> list[dict]:
    """Group an enriched positions list by account category, computing per-category subtotals.

    Returns a list of category-group dicts, each with:
        category, category_display,
        positions (list of position dicts for that category),
        subtotal_cost_cad, subtotal_market_value_cad, subtotal_unrealised_pnl_cad,
        allocation_pct (percentage of total portfolio market value — computed in view)
    """
    groups: dict[str, dict] = {}

    for pos in positions:
        cat = pos["category"]
        if cat not in groups:
            groups[cat] = {
                "category": cat,
                "category_display": pos["category_display"],
                "positions": [],
                "subtotal_cost_cad": Decimal("0"),
                "subtotal_market_value_cad": Decimal("0"),
                "subtotal_unrealised_pnl_cad": Decimal("0"),
            }

        group = groups[cat]
        group["positions"].append(pos)
        group["subtotal_cost_cad"] += pos["total_cost_cad"]
        if pos.get("market_value_cad") is not None:
            group["subtotal_market_value_cad"] += pos["market_value_cad"]

    # Compute unrealised P&L for each group
    for group in groups.values():
        group["subtotal_unrealised_pnl_cad"] = (
            group["subtotal_market_value_cad"] - group["subtotal_cost_cad"]
        )

    # Sort by fixed order: RRSP, LIRA, TFSA, RESP, TAXABLE, OTHER
    category_order = {
        "RRSP": 0,
        "LIRA": 1,
        "TFSA": 2,
        "RESP": 3,
        "TAXABLE": 4,
        "OTHER": 5,
    }

    result = list(groups.values())
    result.sort(key=lambda g: category_order.get(g["category"], 99))

    return result


def get_top_movers(
    positions: list[dict],
    lookback_days: int = 30,
) -> dict[str, list[dict]]:
    """Compute the top 5 gainers and top 5 losers from a list of positions.

    Uses historical prices from the database (with yfinance fallback) to
    determine the percentage change over the lookback period.

    Args:
        positions: List of position dicts (from compute_positions) with a
            'symbol' key. Only symbols where total_shares > 0 are considered.
        lookback_days: Number of days to look back for price comparison.

    Returns:
        Dict with 'gainers' and 'losers' keys, each containing a list of
        dicts with 'symbol' and 'pct_change' (Decimal, percentage).
    """
    try:
        # Collect unique symbols with active positions
        symbols = [
            p["symbol"]
            for p in positions
            if p.get("total_shares", Decimal("0")) > 0 and p.get("symbol")
        ]
        symbols = list(dict.fromkeys(symbols))  # dedupe while preserving order

        if not symbols:
            return {"gainers": [], "losers": []}

        # Calculate date range
        today = date.today()
        end_date = today
        start_date = today - timedelta(days=lookback_days * 2)  # extra buffer

        # Try to load from DB first
        db_df = load_prices_from_db(symbols, start_date, end_date)

        # Build symbol → {old_price, current_price} dict
        price_changes: dict[str, tuple[Decimal, Decimal]] = {}

        if db_df is not None and not db_df.empty:
            for sym in symbols:
                if sym not in db_df.columns:
                    continue
                col = db_df[sym].dropna()
                if len(col) < 2:
                    continue
                # Earliest available price in range
                old_price = Decimal(str(col.iloc[0]))
                # Latest price in range
                new_price = Decimal(str(col.iloc[-1]))
                if old_price > 0:
                    pct = (new_price - old_price) / old_price * Decimal("100")
                    price_changes[sym] = (pct,)
        else:
            # Fallback: try yfinance for each symbol individually
            for sym in symbols:
                try:
                    hist = yf.download(
                        sym,
                        start=(today - timedelta(days=lookback_days * 2)).strftime(
                            "%Y-%m-%d"
                        ),
                        end=(end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                        progress=False,
                    )
                    if hist is not None and not hist.empty:
                        close_col = (
                            hist["Close"]
                            if "Close" in hist.columns
                            else hist.iloc[:, 0]
                        )
                        close_col = close_col.dropna()
                        if len(close_col) >= 2:
                            old_price = Decimal(str(close_col.iloc[0]))
                            new_price = Decimal(str(close_col.iloc[-1]))
                            if old_price > 0:
                                pct = (
                                    (new_price - old_price) / old_price * Decimal("100")
                                )
                                price_changes[sym] = (pct,)
                except Exception as exc:
                    logger.warning(f"get_top_movers yfinance error for {sym}: {exc}")

        # Separate into gainers and losers
        gainers = sorted(
            [
                {"symbol": s, "pct_change": v[0]}
                for s, v in price_changes.items()
                if v[0] > 0
            ],
            key=lambda x: x["pct_change"],
            reverse=True,
        )[:5]
        losers = sorted(
            [
                {"symbol": s, "pct_change": v[0]}
                for s, v in price_changes.items()
                if v[0] < 0
            ],
            key=lambda x: x["pct_change"],
        )[:5]

        return {"gainers": gainers, "losers": losers}
    except Exception as exc:
        logger.warning(f"get_top_movers error: {exc}")
        return {"gainers": [], "losers": []}

