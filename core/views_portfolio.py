"""Portfolio, dashboard, and realised P&L views."""

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render

from core.models import Account, BrokerStatement, FileImportLog, Transaction
from core.utils import (
    compute_cost_basis_series,
    compute_income_series,
    compute_portfolio_value_series,
    compute_positions,
    compute_realised_pnl,
    compute_series_by_account,
    enrich_positions_with_prices,
    fetch_last_prices,
    get_top_movers,
    get_upcoming_dividends,
    group_positions_by_account,
    group_positions_by_category,
)
from core.views import (
    INCOME_TRANSACTION_TYPES,
    _validate_date_param,
    utils_fetch_last_prices,
)

logger = logging.getLogger(__name__)


@login_required
def dashboard(request):
    """Dashboard view with summary stats and dollar-value cards."""
    from datetime import date
    from decimal import Decimal

    from django.db.models import Sum

    accounts = Account.objects.all()
    total_accounts = accounts.count()
    total_transactions = Transaction.objects.count()
    total_brokers = (
        BrokerStatement.objects.values_list("source", flat=True).distinct().count()
    )

    # Get recent transactions - configurable limit (see DASHBOARD_RECENT_LIMIT in settings)
    recent_transactions = Transaction.objects.select_related(
        "account", "statement"
    ).order_by("-date")[: settings.DASHBOARD_RECENT_LIMIT]

    # Get recent imports - same configurable limit
    recent_imports = FileImportLog.objects.order_by("-imported_at")[
        : settings.DASHBOARD_RECENT_LIMIT
    ]

    # --- D3: Dollar summary ---
    try:
        positions = compute_positions()
        symbols = [p["symbol"] for p in positions if p.get("symbol")]
        prices = fetch_last_prices(symbols)  # noqa: F821 - imported above
        positions = enrich_positions_with_prices(positions, prices)

        total_market_value_cad = sum(
            p["market_value_cad"]
            for p in positions
            if p.get("market_value_cad") is not None
        )
        total_market_value_cad = total_market_value_cad or Decimal("0")

        total_unrealised_pnl_cad = sum(
            (p["market_value_cad"] - p["total_cost_cad"])
            for p in positions
            if p.get("market_value_cad") is not None
        )
        total_unrealised_pnl_cad = total_unrealised_pnl_cad or Decimal("0")

        ytd_income_cad = Transaction.objects.filter(
            type__in=("DIV", "DIST", "INT"),
            date__year=date.today().year,
        ).aggregate(total=Sum("amount_cad"))["total"] or Decimal("0")

        # --- D6: Top movers ---
        top_movers = get_top_movers(positions, lookback_days=30)

        # --- D7: Upcoming dividends ---
        upcoming_dividends = get_upcoming_dividends(days_ahead=30)
        total_upcoming_cad = (
            sum(d["total_cad"] for d in upcoming_dividends)
            if upcoming_dividends
            else Decimal("0")
        )
    except Exception as exc:
        logger.warning(f"Dashboard dollar summary error: {exc}")
        total_market_value_cad = Decimal("0")
        total_unrealised_pnl_cad = Decimal("0")
        ytd_income_cad = Decimal("0")
        top_movers = {"gainers": [], "losers": []}
        upcoming_dividends = []
        total_upcoming_cad = Decimal("0")

    return render(
        request,
        "core/dashboard.html",
        {
            "accounts": accounts,
            "total_accounts": total_accounts,
            "total_transactions": total_transactions,
            "total_brokers": total_brokers,
            "recent_transactions": recent_transactions,
            "recent_imports": recent_imports,
            "total_market_value_cad": total_market_value_cad,
            "total_unrealised_pnl_cad": total_unrealised_pnl_cad,
            "ytd_income_cad": ytd_income_cad,
            "top_movers": top_movers,
            "upcoming_dividends": upcoming_dividends,
            "total_upcoming_cad": total_upcoming_cad,
        },
    )


@login_required
def portfolio_summary(request):
    """Portfolio summary with live prices and P&L."""
    logger.info("=== Portfolio Summary Page Requested ===")

    # Compute positions
    positions = compute_positions()
    logger.info(f"Computed {len(positions)} positions")

    # Get unique symbols
    symbols = list(set(pos["symbol"] for pos in positions))
    logger.info(f"Unique symbols: {symbols}")

    # Fetch last prices
    prices = utils_fetch_last_prices(symbols)
    logger.info(f"Fetched {len(prices)} prices")

    # Enrich positions with prices
    enriched_positions = enrich_positions_with_prices(positions, prices)

    # Compute totals
    total_cost_cad = sum(pos["total_cost_cad"] for pos in enriched_positions)
    total_market_value_cad = sum(
        pos["market_value_cad"]
        for pos in enriched_positions
        if pos["market_value_cad"] is not None
    )
    total_unrealised_pnl_cad = sum(
        pos["unrealised_pnl_cad"]
        for pos in enriched_positions
        if pos["unrealised_pnl_cad"] is not None
    )

    # Total Return metrics
    all_realised = compute_realised_pnl()
    total_realised_pnl_cad = sum(t["realised_pnl_fifo"] for t in all_realised)

    total_income_cad = Transaction.objects.filter(
        type__in=INCOME_TRANSACTION_TYPES
    ).aggregate(total=Sum("amount_cad"))["total"] or Decimal("0")

    total_return_cad = (
        total_unrealised_pnl_cad + total_realised_pnl_cad + total_income_cad
    )

    # Add allocation_pct to each position
    for pos in enriched_positions:
        if pos.get("market_value_cad") is not None and total_market_value_cad:
            pos["allocation_pct"] = round(
                pos["market_value_cad"] / total_market_value_cad * 100, 2
            )
        else:
            pos["allocation_pct"] = None

    # Group by account
    account_groups = group_positions_by_account(enriched_positions)

    # Group by category
    category_groups = group_positions_by_category(enriched_positions)
    for group in category_groups:
        if total_market_value_cad:
            group["allocation_pct"] = round(
                group["subtotal_market_value_cad"] / total_market_value_cad * 100, 2
            )
        else:
            group["allocation_pct"] = None

    # Build currency breakdown
    currency_breakdown = {}
    for pos in enriched_positions:
        cur = pos["currency"]
        if cur not in currency_breakdown:
            currency_breakdown[cur] = {
                "cost_cad": Decimal("0"),
                "market_value_cad": Decimal("0"),
            }
        currency_breakdown[cur]["cost_cad"] += pos["total_cost_cad"]
        if pos.get("market_value_cad") is not None:
            currency_breakdown[cur]["market_value_cad"] += pos["market_value_cad"]
    currency_breakdown = [
        {"currency": k, **v} for k, v in sorted(currency_breakdown.items())
    ]

    logger.info(
        f"Total Cost: {total_cost_cad}, Market Value: {total_market_value_cad}, P&L: {total_unrealised_pnl_cad}"
    )
    logger.info(
        f"Total Realised: {total_realised_pnl_cad}, Total Income: {total_income_cad}, Total Return: {total_return_cad}"
    )

    return render(
        request,
        "core/portfolio_summary.html",
        {
            "positions": enriched_positions,
            "total_cost_cad": total_cost_cad,
            "total_market_value_cad": total_market_value_cad,
            "total_unrealised_pnl_cad": total_unrealised_pnl_cad,
            "total_realised_pnl_cad": total_realised_pnl_cad,
            "total_income_cad": total_income_cad,
            "total_return_cad": total_return_cad,
            "account_groups": account_groups,
            "category_groups": category_groups,
            "currency_breakdown": currency_breakdown,
        },
    )


@login_required
def realised_pnl(request):
    """Realised P&L page with FIFO and average-cost methods."""
    account = request.GET.get("account", "")
    raw_symbol = request.GET.get("symbol", "").strip().upper()
    # Validate symbol: only allow alphanumeric, dots, hyphens, and slashes (e.g., T.TO, BNS-UN)
    if raw_symbol and not re.match(r"^[A-Z0-9.\-/]+$", raw_symbol):
        raw_symbol = re.sub(r"[^A-Z0-9.\-/]", "", raw_symbol).upper()[:20]
    symbol = raw_symbol[:20]
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    # Validate date parameters — silently discard invalid ones
    validated_date_from = None
    validated_date_to = None
    if date_from:
        try:
            validated_date_from = _validate_date_param(date_from)
        except ValueError:
            validated_date_from = None
    if date_to:
        try:
            validated_date_to = _validate_date_param(date_to)
        except ValueError:
            validated_date_to = None

    trades = compute_realised_pnl(
        account_id=account if account else None,
        symbol=symbol if symbol else None,
        date_from=validated_date_from.strftime("%Y-%m-%d")
        if validated_date_from
        else None,
        date_to=validated_date_to.strftime("%Y-%m-%d") if validated_date_to else None,
    )

    # Compute totals
    total_pnl_fifo = sum(t["realised_pnl_fifo"] for t in trades)
    total_pnl_avg = sum(t["realised_pnl_avg"] for t in trades)

    # Year grouping
    year_groups = defaultdict(list)
    for trade in trades:
        year_groups[trade["sell_date"].year].append(trade)

    year_groups_sorted = sorted(
        [
            {
                "year": year,
                "trades": trade_list,
                "total_pnl_fifo": sum(t["realised_pnl_fifo"] for t in trade_list),
                "total_pnl_avg": sum(t["realised_pnl_avg"] for t in trade_list),
            }
            for year, trade_list in year_groups.items()
        ],
        key=lambda g: g["year"],
        reverse=True,
    )

    # Get all accounts for filter dropdown
    accounts = Account.objects.all().order_by("account_id")

    group_by = request.GET.get("group_by", "flat")

    return render(
        request,
        "core/realised_pnl.html",
        {
            "trades": trades,
            "total_pnl_fifo": total_pnl_fifo,
            "total_pnl_avg": total_pnl_avg,
            "accounts": accounts,
            "account_filter": account,
            "symbol_filter": symbol,
            "date_from_filter": validated_date_from.strftime("%Y-%m-%d")
            if validated_date_from
            else date_from,
            "date_to_filter": validated_date_to.strftime("%Y-%m-%d")
            if validated_date_to
            else date_to,
            "year_groups_sorted": year_groups_sorted,
            "group_by": group_by,
        },
    )


@login_required
def portfolio_chart(request):
    """Render portfolio performance chart template."""
    return render(request, "core/portfolio_chart.html")


@login_required
def portfolio_chart_data(request):
    """JSON endpoint for portfolio chart data with multiple series support."""
    try:
        # Parse query parameters
        date_from = request.GET.get("date_from", "")
        date_to = request.GET.get("date_to", "")
        freq = request.GET.get("freq", "monthly")
        series_param = request.GET.get("series", "market_value")
        benchmark_ticker = request.GET.get("benchmark", "^GSPC")

        # Validate freq
        VALID_FREQS = {"daily", "weekly", "monthly", "annual"}
        if freq not in VALID_FREQS:
            return JsonResponse(
                {
                    "error": f"Invalid freq: {freq}. Must be one of {sorted(VALID_FREQS)}"
                },
                status=400,
            )

        # Validate date_from
        if date_from:
            try:
                datetime.strptime(date_from, "%Y-%m-%d")
            except ValueError:
                return JsonResponse(
                    {"error": f"Invalid date_from format: {date_from}"}, status=400
                )

        # Validate date_to
        if date_to:
            try:
                datetime.strptime(date_to, "%Y-%m-%d")
            except ValueError:
                return JsonResponse(
                    {"error": f"Invalid date_to format: {date_to}"}, status=400
                )

        # Sanitize benchmark ticker
        if benchmark_ticker:
            # Strip non-alphanumeric except '.', '^', '='
            sanitized = re.sub(r"[^a-zA-Z0-9.^=]", "", benchmark_ticker)
            if len(sanitized) > 20:
                sanitized = sanitized[:20]
            benchmark_ticker = sanitized

        # Parse series list
        series_list = [s.strip() for s in series_param.split(",") if s.strip()]
        if not series_list:
            series_list = ["market_value"]

        # Build response
        labels = []
        datasets = []

        # Always include market_value
        mv_series = None
        if "market_value" in series_list or not series_list:
            try:
                mv_series = compute_portfolio_value_series(
                    freq=freq, date_from=date_from, date_to=date_to
                )
                if mv_series:
                    labels = [item["date"] for item in mv_series]
                    values = [float(item["market_value_cad"]) for item in mv_series]
                    datasets.append(
                        {
                            "id": "market_value",
                            "label": "Portfolio Market Value (CAD)",
                            "data": values,
                            "type": "line",
                            "yAxisID": "y",
                        }
                    )
            except Exception as e:
                logger.error(f"Error computing market value series: {e}")

        # Optional: cost_basis
        if "cost_basis" in series_list:
            try:
                cb_series = compute_cost_basis_series(
                    date_from=date_from, date_to=date_to, freq=freq
                )
                cb_values = [float(item["cost_basis_cad"]) for item in cb_series]
                # Normalize: treat the start of the range as "bought at market price that day"
                # so cost basis starts at the same value as portfolio market value at t0
                if (
                    mv_series
                    and cb_values
                    and float(mv_series[0]["market_value_cad"]) > 0
                ):
                    mv0 = float(mv_series[0]["market_value_cad"])
                    cb0 = cb_values[0]
                    cb_values = [mv0 + (v - cb0) for v in cb_values]
                datasets.append(
                    {
                        "id": "cost_basis",
                        "label": "Cost Basis (CAD)",
                        "data": cb_values,
                        "type": "line",
                        "yAxisID": "y",
                    }
                )
            except Exception as e:
                logger.error(f"Error computing cost basis series: {e}")

        # Optional: income
        # For daily/weekly, income stays monthly so it remains readable.
        # Only for annual do we pass annual freq to income.
        if "income" in series_list:
            try:
                income_freq = freq if freq == "annual" else "monthly"
                inc_series = compute_income_series(
                    date_from=date_from, date_to=date_to, freq=income_freq
                )
                inc_values = [float(item["income_cad"]) for item in inc_series]
                datasets.append(
                    {
                        "id": "income",
                        "label": "Monthly Income (CAD)",
                        "data": inc_values,
                        "type": "bar",
                        "yAxisID": "y2",
                    }
                )
            except Exception as e:
                logger.error(f"Error computing income series: {e}")

        # Optional: benchmark — same starting balance, grows/falls with index returns.
        # Transaction-based cash-flow matching is not used because round-trip trades
        # (buy and sell within a period) cause the benchmark to diverge incorrectly.
        if "benchmark" in series_list:
            try:
                import pandas as pd

                from core.models import Transaction as TxModel
                from core.utils import _load_prices_from_db, _load_prices_from_yfinance

                first_tx = (
                    TxModel.objects.order_by("date")
                    .values_list("date", flat=True)
                    .first()
                )
                bench_start = (
                    first_tx if first_tx else (date.today() - timedelta(days=3650))
                )
                bench_end = date.today()

                bench_df = _load_prices_from_db(
                    [benchmark_ticker], bench_start, bench_end
                )
                if bench_df is None or benchmark_ticker not in bench_df.columns:
                    bench_df = _load_prices_from_yfinance(
                        [benchmark_ticker], bench_start, bench_end
                    )

                bench_prices_series = None
                if bench_df is not None and benchmark_ticker in bench_df.columns:
                    bench_prices_series = bench_df[benchmark_ticker].dropna()

                # Start at portfolio market value at t0 and grow with index returns only.
                mv0 = float(mv_series[0]["market_value_cad"]) if mv_series else 0.0
                bench_portfolio = mv0
                prev_bench_price = None
                bench_initialized = False
                bench_values = []

                for lbl in labels:
                    bench_price = None
                    if bench_prices_series is not None:
                        raw = bench_prices_series.asof(pd.Timestamp(lbl[:10]))
                        if raw is not None and not (
                            isinstance(raw, float) and pd.isna(raw)
                        ):
                            bench_price = float(raw)

                    if bench_price is None:
                        bench_values.append(None)
                        continue

                    if not bench_initialized:
                        bench_initialized = True
                        prev_bench_price = bench_price
                        bench_values.append(max(0.0, bench_portfolio))
                        continue

                    if prev_bench_price > 0:
                        bench_portfolio *= bench_price / prev_bench_price

                    prev_bench_price = bench_price
                    bench_values.append(max(0.0, bench_portfolio))

                safe_id = (
                    benchmark_ticker.replace(".", "_")
                    .replace("^", "_")
                    .replace("=", "_")
                )
                datasets.append(
                    {
                        "id": f"benchmark_{safe_id}",
                        "label": f"{benchmark_ticker} (if invested same)",
                        "data": bench_values,
                        "type": "line",
                        "yAxisID": "y",
                    }
                )
            except Exception as e:
                logger.error(f"Error computing benchmark series: {e}")

        # Optional: by_account
        if "by_account" in series_list:
            try:
                account_series = compute_series_by_account(
                    date_from=date_from, date_to=date_to, freq=freq
                )
                account_colors = [
                    "#4e79a7",
                    "#f28e2b",
                    "#e15759",
                    "#76b7b2",
                    "#59a14f",
                    "#edc948",
                ]

                for idx, (acct_id, acct_data) in enumerate(account_series.items()):
                    color = account_colors[idx % len(account_colors)]
                    datasets.append(
                        {
                            "id": f"account_{acct_id}",
                            "label": f"Account {acct_id}",
                            "data": [
                                float(item["market_value_cad"]) for item in acct_data
                            ],
                            "type": "line",
                            "yAxisID": "y",
                            "borderColor": color,
                            "backgroundColor": color,
                        }
                    )
            except Exception as e:
                logger.error(f"Error computing series by account: {e}")

        return JsonResponse(
            {
                "labels": labels,
                "datasets": datasets,
                "freq": freq,
            }
        )
    except Exception as e:
        logger.exception("Error in portfolio_chart_data")
        return JsonResponse({"labels": [], "datasets": [], "error": str(e)}, status=500)
