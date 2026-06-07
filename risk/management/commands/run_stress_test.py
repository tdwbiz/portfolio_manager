"""
Run portfolio stress test with named or custom scenarios.

Usage examples:
    python manage.py run_stress_test \\
        --scenario 2022_rate_shock \\
        --date 2026-06-02 \\
        --settings=portfolio_manager.settings_dev

    python manage.py run_stress_test \\
        --custom-scenario 5 \\
        --account TFSA_TOM \\
        --settings=portfolio_manager.settings_dev

    python manage.py run_stress_test \\
        --scenario 2020_covid_crash \\
        --strategy 123 \\
        --settings=portfolio_manager.settings_dev

Options:
    --scenario           Named scenario from rm_historical_scenario
    --custom-scenario    rm_custom_stress_scenario.id for user-defined shocks
    --account            Restrict to one account (default: whole portfolio)
    --date               Use positions/prices as of this date (default: today)
    --strategy           rm_strategy_recommendation.id for hedged comparison
"""

import json
import logging
import time
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from risk.models import (
    CustomStressScenario,
    HistoricalScenario,
    SimulationResult,
    SimulationRun,
    StrategyRecommendation,
)
from risk.services.stress_test import run_portfolio_stress_test

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run portfolio stress test with named or custom scenarios."

    def add_arguments(self, parser):
        parser.add_argument(
            "--scenario",
            type=str,
            default=None,
            help="Named scenario from rm_historical_scenario (e.g. 2022_rate_shock)",
        )
        parser.add_argument(
            "--custom-scenario",
            type=int,
            default=None,
            help="rm_custom_stress_scenario.id for user-defined shocks",
        )
        parser.add_argument(
            "--account",
            type=str,
            default=None,
            help="Restrict to one account (default: whole portfolio)",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Use positions/prices as of this date (YYYY-MM-DD, default: today)",
        )
        parser.add_argument(
            "--strategy",
            type=int,
            default=None,
            help="rm_strategy_recommendation.id for hedged comparison",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        scenario_name = options["scenario"]
        custom_scenario_id = options["custom_scenario"]
        account_id = options["account"]
        strategy_id = options["strategy"]

        # Determine calc_date
        if options["date"]:
            try:
                calc_date = date.fromisoformat(options["date"])
            except ValueError:
                raise CommandError(f"Invalid date format: {options['date']} (use YYYY-MM-DD)")
        else:
            calc_date = date.today()

        # Load data source
        from risk.services.data_source import DataSource
        ds = DataSource()

        # Load shock map
        shock_map, scenario_label = self._load_shock_map(
            scenario_name, custom_scenario_id, ds
        )

        if not shock_map:
            raise CommandError("No shocks defined in scenario")

        # Load positions
        if account_id:
            positions = ds.get_portfolio_positions(entity_id=account_id, calc_date=calc_date)
            entity_type = "account"
            entity_id = account_id
        else:
            positions = ds.get_portfolio_positions(entity_id="ALL", calc_date=calc_date)
            entity_type = "portfolio"
            entity_id = "ALL"

        if not positions:
            raise CommandError(f"No positions found for {entity_type}={entity_id} on {calc_date}")

        # Load current prices
        current_prices = {}
        for p in positions:
            sym = p["symbol"]
            if sym not in current_prices:
                price_info = ds.get_current_price(sym, calc_date=calc_date)
                if price_info:
                    current_prices[sym] = float(price_info.get("price_usd", 0.0))
                else:
                    self.stdout.write(f"WARNING: No price for {sym}, skipping")

        # Load FX rate
        fx_rate = self._get_fx_rate(calc_date, ds)

        # Load security universe for asset class lookup
        security_universe = {}
        try:
            from risk.models import SecurityUniverse
            for sym in current_prices:
                entry = SecurityUniverse.objects.filter(symbol=sym).first()
                if entry:
                    security_universe[sym] = {
                        "asset_class": entry.asset_class,
                        "sector": entry.sector or "",
                    }
        except Exception:
            pass

        # Load strategy overlay if provided
        strategy_overlay = None
        if strategy_id is not None:
            try:
                rec = StrategyRecommendation.objects.get(id=strategy_id)
                strategy_overlay = {
                    "strategy_code": rec.strategy_template.code,
                    "put_strike_pct": 0.85,
                    "trail_pct": 0.10,
                    "quantity": float(rec.position_quantity),
                }
            except StrategyRecommendation.DoesNotExist:
                raise CommandError(f"Strategy recommendation {strategy_id} not found")

        # Run stress test
        start_time = time.time()

        self.stdout.write(f"Running stress test: {scenario_label}")
        self.stdout.write(f"Positions: {len(positions)} | Date: {calc_date}")
        self.stdout.write(f"Shocks: {len(shock_map)} keys")

        result_data = run_portfolio_stress_test(
            positions=positions,
            shock_map=shock_map,
            current_prices=current_prices,
            fx_rate=fx_rate,
            strategy_overlay=strategy_overlay,
            security_universe=security_universe,
        )

        duration = time.time() - start_time

        # Create SimulationRun
        run = SimulationRun.objects.create(
            simulation_type="stress_test" if scenario_name else "custom_stress",
            entity_type=entity_type,
            entity_id=entity_id,
            scenario_name=scenario_label,
            custom_shocks_json=json.dumps(shock_map),
            recommendation_id=strategy_id,
            calc_date=calc_date,
            run_by="system",
            duration_seconds=Decimal(str(round(duration, 2))),
        )

        # Create SimulationResult
        SimulationResult.objects.create(
            run=run,
            portfolio_value_start_usd=result_data["total_mv_usd"],
            stress_loss_unhedged_usd=result_data["stress_loss_unhedged_usd"],
            stress_loss_unhedged_pct=result_data["stress_loss_unhedged_pct"],
            stress_loss_hedged_usd=result_data["stress_loss_hedged_usd"],
            stress_loss_hedged_pct=result_data["stress_loss_hedged_pct"],
            stress_per_position_json=result_data["stress_per_position_json"],
        )

        # Print summary
        self.stdout.write(self.style.SUCCESS("\n=== Stress Test Complete ==="))
        self.stdout.write(f"Entity: {entity_type} = {entity_id}")
        self.stdout.write(f"Scenario: {scenario_label}")
        self.stdout.write(f"Date: {calc_date}")
        self.stdout.write(f"Duration: {duration:.2f}s")
        self.stdout.write(f"\n--- Portfolio Loss ---")
        self.stdout.write(f"  Unhedged: ${result_data['stress_loss_unhedged_usd']:>14,.2f} "
                          f"({result_data['stress_loss_unhedged_pct']:.2%})")
        self.stdout.write(f"  Hedged:   ${result_data['stress_loss_hedged_usd']:>14,.2f} "
                          f"({result_data['stress_loss_hedged_pct']:.2%})")

        # Print per-position breakdown
        per_position = json.loads(result_data["stress_per_position_json"])
        self.stdout.write(f"\n--- Per-Position Breakdown ({len(per_position)} positions) ---")
        self.stdout.write(f"  {'Symbol':<10} {'Account':<16} {'Qty':>8} {'Price':>10} {'MV':>12} {'Shock':>8} {'Loss':>12}")
        self.stdout.write("  " + "-" * 78)
        for pp in per_position:
            self.stdout.write(
                f"  {pp['symbol']:<10} {pp['account']:<16} {pp['quantity']:>8.0f} "
                f"${pp['price_usd']:>9.2f} ${pp['mv_usd']:>11,.2f} "
                f"{pp['shock_pct']:>7.1%} ${pp['loss_usd']:>11,.2f}"
            )

        self.stdout.write(f"\nRun ID: {run.id} | Result ID: {run.result.id}")

    def _load_shock_map(
        self,
        scenario_name: str | None,
        custom_scenario_id: int | None,
        ds,
    ) -> tuple[dict[str, float], str]:
        """Load shock map from named scenario or custom scenario."""
        if scenario_name:
            try:
                scenario = HistoricalScenario.objects.get(name=scenario_name)
            except HistoricalScenario.DoesNotExist:
                raise CommandError(f"Scenario '{scenario_name}' not found")

            asset_class_returns = json.loads(scenario.asset_class_returns_json)
            return asset_class_returns, scenario.display_name

        elif custom_scenario_id is not None:
            try:
                custom = CustomStressScenario.objects.get(id=custom_scenario_id, is_active=True)
            except CustomStressScenario.DoesNotExist:
                raise CommandError(f"Custom scenario {custom_scenario_id} not found or inactive")

            shocks = json.loads(custom.shocks_json)
            return shocks, custom.name

        else:
            raise CommandError("Provide either --scenario or --custom-scenario")

    def _get_fx_rate(self, calc_date, ds) -> float:
        """Get CAD/USD FX rate for calc_date."""
        try:
            from risk.models import FxRate
            rate = FxRate.objects.filter(date=calc_date, currency_pair="CADUSD").first()
            if rate:
                return float(rate.rate)
        except Exception:
            pass
        # Default fallback
        return 1.384
