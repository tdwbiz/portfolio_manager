"""
Run Monte Carlo simulation for portfolio risk analysis.

Usage examples:
    python manage.py run_montecarlo \\
        --entity portfolio \\
        --n 10000 \\
        --horizon 252 \\
        --date 2026-06-02 \\
        --seed 42 \\
        --settings=portfolio_manager.settings_dev

    python manage.py run_montecarlo \\
        --entity symbol \\
        --entity-id AAPL \\
        --n 10000 \\
        --horizon 252 \\
        --strategy 123 \\
        --seed 42 \\
        --settings=portfolio_manager.settings_dev

    python manage.py run_montecarlo \\
        --entity account \\
        --entity-id TFSA_TOM \\
        --n 5000 \\
        --horizon 63 \\
        --use-jump-diffusion \\
        --settings=portfolio_manager.settings_dev

Options:
    --entity              Scope: portfolio, account, or symbol (default: portfolio)
    --entity-id           Account ID, symbol, or 'ALL' (default: ALL)
    --n                   Number of paths (default: 10000)
    --horizon             Trading days (default: 252)
    --date                Use positions/prices as of this date (default: today)
    --strategy            rm_strategy_recommendation.id for hedged comparison
    --seed                Integer seed for reproducibility
    --use-jump-diffusion  Override: apply jump-diffusion to all symbols
"""

import json
import logging
import time
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from risk.models import (
    SimulationResult,
    SimulationRun,
    StrategyRecommendation,
)
from risk.services.monte_carlo import (
    apply_strategy_overlay,
    compute_output_distribution,
    generate_correlated_paths,
)
from risk.services.data_source import DataSource

logger = logging.getLogger(__name__)

# Default jump-diffusion config keys in rm_scoring_config
JUMP_CONFIG_KEYS = {
    "crypto_intensity": "mc_jump_intensity_crypto",
    "crypto_mean": "mc_jump_mean_crypto",
    "crypto_std": "mc_jump_std_crypto",
    "nvda_intensity": "mc_jump_intensity_nvda",
    "nvda_mean": "mc_jump_mean_nvda",
    "nvda_std": "mc_jump_std_nvda",
}


class Command(BaseCommand):
    help = "Run Monte Carlo simulation for portfolio risk analysis."

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity",
            type=str,
            default="portfolio",
            choices=["portfolio", "account", "symbol"],
            help="Scope of simulation (default: portfolio)",
        )
        parser.add_argument(
            "--entity-id",
            type=str,
            default="ALL",
            help="Account ID, symbol, or 'ALL' (default: ALL)",
        )
        parser.add_argument(
            "--n",
            type=int,
            default=10000,
            help="Number of paths (default: 10000)",
        )
        parser.add_argument(
            "--horizon",
            type=int,
            default=252,
            help="Trading days (default: 252)",
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
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Integer seed for reproducibility",
        )
        parser.add_argument(
            "--use-jump-diffusion",
            action="store_true",
            default=False,
            help="Override: apply jump-diffusion to all symbols",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        entity_type = options["entity"]
        entity_id = options["entity_id"]
        n_paths = options["n"]
        horizon_days = options["horizon"]
        strategy_id = options["strategy"]
        seed = options["seed"]
        use_jump = options["use_jump_diffusion"]

        # Determine calc_date
        if options["date"]:
            try:
                calc_date = date.fromisoformat(options["date"])
            except ValueError:
                raise CommandError(f"Invalid date format: {options['date']} (use YYYY-MM-DD)")
        else:
            calc_date = date.today()

        # Set random seed
        if seed is not None:
            np.random.seed(seed)
            rng = np.random.default_rng(seed)
        else:
            rng = np.random.default_rng()

        # Load data source
        ds = DataSource()

        # Load positions
        if entity_type == "portfolio":
            positions = ds.get_portfolio_positions(entity_id="ALL", calc_date=calc_date)
        elif entity_type == "account":
            positions = ds.get_portfolio_positions(entity_id=entity_id, calc_date=calc_date)
        elif entity_type == "symbol":
            positions = ds.get_position_by_symbol(entity_id, calc_date=calc_date)
        else:
            raise CommandError(f"Invalid entity type: {entity_type}")

        if not positions:
            raise CommandError(f"No positions found for {entity_type}={entity_id} on {calc_date}")

        # Build symbol lists and parameters
        symbols = list({p["symbol"] for p in positions})
        symbol_set = set(symbols)

        # Load volatility data
        vol_data = ds.get_volatility_summary(symbols=symbols, calc_date=calc_date)

        # Load drift (mean daily log returns from price history)
        drift_data = {}
        for sym in symbols:
            prices = ds.get_price_series(sym, start_date=calc_date - timedelta(days=365), end_date=calc_date)
            if prices and len(prices) > 1:
                log_returns = np.log(prices[1:] / prices[:-1])
                drift_data[sym] = float(np.mean(log_returns) * 252)  # annualise
            else:
                drift_data[sym] = 0.05  # fallback 5% annual drift

        # Build price and volatility dicts
        S0_values = {}
        sigma_values = {}
        for sym in symbols:
            # Get current price
            price_info = ds.get_current_price(sym, calc_date=calc_date)
            if price_info:
                S0_values[sym] = float(price_info.get("price_usd", 0.0))
            else:
                raise CommandError(f"No price data for {sym} on {calc_date}")

            # Get volatility
            vol_entry = vol_data.get(sym, {})
            sigma_values[sym] = float(vol_entry.get("vol_252d", 0.20))

        # Build position weights
        total_mv = sum(p.get("market_value_usd", 0) for p in positions)
        if total_mv <= 0:
            raise CommandError(f"Total market value is zero for {entity_type}={entity_id}")

        position_weights = {}
        symbol_positions = {}
        for p in positions:
            sym = p["symbol"]
            mv = p.get("market_value_usd", 0)
            position_weights[sym] = mv / total_mv
            symbol_positions.setdefault(sym, []).append(p)

        # Load correlation matrix for portfolio-level runs
        correlation_matrix = None
        if entity_type == "portfolio":
            corr_data = ds.get_correlation_matrix(calc_date=calc_date)
            if corr_data and len(symbols) <= 50:
                corr_matrix_arr = ds.build_correlation_matrix_array(symbols, corr_data)
                if corr_matrix_arr is not None:
                    correlation_matrix = corr_matrix_arr

        # Load jump-diffusion config from rm_scoring_config
        jump_params = self._load_jump_config()
        use_jump_flags = {}
        for sym in symbols:
            if use_jump:
                use_jump_flags[sym] = True
            else:
                # Check if symbol is flagged in security_universe
                use_jump_flags[sym] = self._is_jump_symbol(sym, ds)

        # Load strategy overlay if provided
        strategy_overlay = None
        recommendation = None
        if strategy_id is not None:
            try:
                recommendation = StrategyRecommendation.objects.get(id=strategy_id)
                strategy_overlay = {
                    "strategy_code": recommendation.strategy_template.code,
                    "put_strike_pct": 0.85,
                    "trail_pct": 0.10,
                    "quantity": float(recommendation.position_quantity),
                }
            except StrategyRecommendation.DoesNotExist:
                raise CommandError(f"Strategy recommendation {strategy_id} not found")

        # Start simulation
        start_time = time.time()

        self.stdout.write(f"Running Monte Carlo: {n_paths} paths, {horizon_days} days")
        self.stdout.write(f"Symbols: {', '.join(symbols)}")
        self.stdout.write(f"Entity: {entity_type}={entity_id}, Date: {calc_date}")

        if correlation_matrix is not None:
            # Generate correlated paths
            symbol_paths, portfolio_paths = generate_correlated_paths(
                symbols=symbols,
                S0_values=S0_values,
                mu_values=drift_data,
                sigma_values=sigma_values,
                correlation_matrix=correlation_matrix,
                position_weights=position_weights,
                horizon_days=horizon_days,
                n_paths=n_paths,
                use_jump_diffusion_flags=use_jump_flags,
                jump_params=jump_params,
                rng=rng,
            )
        else:
            # Fallback: generate independent paths (no correlation)
            self.stdout.write("WARNING: No correlation matrix available; using independent paths")
            symbol_paths, portfolio_paths = self._generate_independent_paths(
                symbols=symbols,
                S0_values=S0_values,
                mu_values=drift_data,
                sigma_values=sigma_values,
                position_weights=position_weights,
                horizon_days=horizon_days,
                n_paths=n_paths,
                use_jump_diffusion_flags=use_jump_flags,
                jump_params=jump_params,
                rng=rng,
            )

        # Apply strategy overlay if provided
        if strategy_overlay and recommendation:
            sym_index = symbols.index(recommendation.symbol)
            pos_weight = position_weights.get(recommendation.symbol, 0.0)
            vol_ann = sigma_values.get(recommendation.symbol, 0.20)

            strategy_params = {
                "trail_pct": strategy_overlay.get("trail_pct", 0.10),
                "put_strike_pct": strategy_overlay.get("put_strike_pct", 0.85),
                "quantity": strategy_overlay.get("quantity", 100),
            }

            portfolio_paths_hedged = apply_strategy_overlay(
                portfolio_paths=portfolio_paths,
                symbol_paths=symbol_paths,
                strategy_code=strategy_overlay["strategy_code"],
                strategy_params=strategy_params,
                position_symbol=recommendation.symbol,
                symbol_index=sym_index,
                position_weight=pos_weight,
                vol_annualised=vol_ann,
                hedge_cost_per_unit=0.0,  # Would need option pricing service
            )
        else:
            portfolio_paths_hedged = None

        # Compute output distribution
        total_mv_usd = float(total_mv)
        dist = compute_output_distribution(portfolio_paths, total_mv_usd)

        # Compute hedged distribution if applicable
        dist_hedged = None
        if portfolio_paths_hedged is not None:
            dist_hedged = compute_output_distribution(portfolio_paths_hedged, total_mv_usd)

        # Calculate duration
        duration = time.time() - start_time

        # Create SimulationRun
        run = SimulationRun.objects.create(
            simulation_type="montecarlo",
            entity_type=entity_type,
            entity_id=entity_id,
            n_paths=n_paths,
            horizon_days=horizon_days,
            use_jump_diffusion=any(use_jump_flags.values()),
            random_seed=seed,
            recommendation_id=strategy_id,
            calc_date=calc_date,
            run_by="system",
            duration_seconds=Decimal(str(round(duration, 2))),
        )

        # Build SimulationResult
        result_kwargs = {
            "run": run,
            "portfolio_value_start_usd": total_mv_usd,
            "pct5_outcome_usd": dist["pct5"],
            "pct25_outcome_usd": dist["pct25"],
            "pct50_outcome_usd": dist["pct50"],
            "pct75_outcome_usd": dist["pct75"],
            "pct95_outcome_usd": dist["pct95"],
            "prob_loss_5pct": dist["prob_loss_5pct"],
            "prob_loss_10pct": dist["prob_loss_10pct"],
            "prob_loss_20pct": dist["prob_loss_20pct"],
            "prob_loss_50pct": dist["prob_loss_50pct"],
            "expected_shortfall_5pct_usd": dist["expected_shortfall"],
            "expected_shortfall_pct": dist["expected_shortfall_pct"],
            "percentile_series_json": dist["percentile_series_json"],
        }

        # Add hedged fields if available
        if dist_hedged:
            result_kwargs.update({
                "pct5_outcome_hedged_usd": dist_hedged["pct5"],
                "pct50_outcome_hedged_usd": dist_hedged["pct50"],
                "pct95_outcome_hedged_usd": dist_hedged["pct95"],
                "prob_loss_10pct_hedged": dist_hedged["prob_loss_10pct"],
                "expected_shortfall_5pct_hedged_usd": dist_hedged["expected_shortfall"],
                "hedge_cost_total_usd": 0.0,  # Would need option pricing
            })

        SimulationResult.objects.create(**result_kwargs)

        # Print summary
        self.stdout.write(self.style.SUCCESS("\n=== Monte Carlo Simulation Complete ==="))
        self.stdout.write(f"Entity: {entity_type} = {entity_id}")
        self.stdout.write(f"Date: {calc_date}")
        self.stdout.write(f"Paths: {n_paths:,} | Horizon: {horizon_days} days")
        self.stdout.write(f"Duration: {duration:.2f}s")
        self.stdout.write(f"\n--- Unhedged Outcome Distribution ---")
        self.stdout.write(f"  P5:  ${dist['pct5']:>14,.2f}")
        self.stdout.write(f"  P25: ${dist['pct25']:>14,.2f}")
        self.stdout.write(f"  P50: ${dist['pct50']:>14,.2f}")
        self.stdout.write(f"  P75: ${dist['pct75']:>14,.2f}")
        self.stdout.write(f"  P95: ${dist['pct95']:>14,.2f}")
        self.stdout.write(f"\n--- Loss Probabilities ---")
        self.stdout.write(f"  P(loss > 5%):  {dist['prob_loss_5pct']:.2%}")
        self.stdout.write(f"  P(loss > 10%): {dist['prob_loss_10pct']:.2%}")
        self.stdout.write(f"  P(loss > 20%): {dist['prob_loss_20pct']:.2%}")
        self.stdout.write(f"  P(loss > 50%): {dist['prob_loss_50pct']:.2%}")
        self.stdout.write(f"\n--- Expected Shortfall (95%) ---")
        self.stdout.write(f"  ES: ${dist['expected_shortfall']:>14,.2f} ({dist['expected_shortfall_pct']:.2%} loss)")

        if dist_hedged:
            self.stdout.write(f"\n--- Hedged Outcome Distribution ---")
            self.stdout.write(f"  P5:  ${dist_hedged['pct5']:>14,.2f}")
            self.stdout.write(f"  P50: ${dist_hedged['pct50']:>14,.2f}")
            self.stdout.write(f"  P95: ${dist_hedged['pct95']:>14,.2f}")
            self.stdout.write(f"  P(loss > 10%): {dist_hedged['prob_loss_10pct']:.2%}")
            self.stdout.write(f"  ES: ${dist_hedged['expected_shortfall']:>14,.2f}")

        self.stdout.write(f"\nRun ID: {run.id} | Result ID: {run.result.id}")

    def _load_jump_config(self) -> dict[str, dict[str, float]]:
        """Load jump-diffusion parameters from rm_scoring_config."""
        from risk.models import ScoringConfig

        try:
            config = ScoringConfig.objects.all()
            cfg = {c.key: float(c.value) for c in config if c.is_active}
        except Exception:
            cfg = {}

        return {
            "crypto": {
                "jump_intensity": cfg.get("mc_jump_intensity_crypto", 5.0),
                "jump_mean": cfg.get("mc_jump_mean_crypto", -0.04),
                "jump_std": cfg.get("mc_jump_std_crypto", 0.08),
            },
            "nvda": {
                "jump_intensity": cfg.get("mc_jump_intensity_nvda", 3.0),
                "jump_mean": cfg.get("mc_jump_mean_nvda", -0.02),
                "jump_std": cfg.get("mc_jump_std_nvda", 0.06),
            },
        }

    def _is_jump_symbol(self, symbol: str, ds: DataSource) -> bool:
        """Check if a symbol is flagged for jump-diffusion in security_universe."""
        try:
            from risk.models import SecurityUniverse
            entry = SecurityUniverse.objects.filter(symbol=symbol).first()
            return bool(entry and entry.use_jump_diffusion)
        except Exception:
            # Crypto ETFs and NVDA typically use jump-diffusion
            crypto_symbols = {"IBIT", "ETHA", "ETHE", "BITB", "SOLC"}
            return symbol in crypto_symbols or symbol == "NVDA"

    def _generate_independent_paths(
        self,
        symbols: list[str],
        S0_values: dict[str, float],
        mu_values: dict[str, float],
        sigma_values: dict[str, float],
        position_weights: dict[str, float],
        horizon_days: int,
        n_paths: int,
        use_jump_diffusion_flags: dict[str, bool],
        jump_params: dict[str, dict[str, float]],
        rng: np.random.Generator,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate independent (uncorrelated) paths as fallback."""
        from risk.services.monte_carlo import generate_gbm_paths, generate_jump_diffusion_paths

        symbol_paths_list = []
        for sym in symbols:
            if use_jump_diffusion_flags.get(sym, False):
                jp = jump_params.get(sym, jump_params.get("crypto", {}))
                paths = generate_jump_diffusion_paths(
                    S0=S0_values[sym],
                    mu=mu_values[sym],
                    sigma=sigma_values[sym],
                    jump_intensity=jp.get("jump_intensity", 5.0),
                    jump_mean=jp.get("jump_mean", -0.04),
                    jump_std=jp.get("jump_std", 0.08),
                    horizon_days=horizon_days,
                    n_paths=n_paths,
                    rng=rng,
                )
            else:
                paths = generate_gbm_paths(
                    S0=S0_values[sym],
                    mu=mu_values[sym],
                    sigma=sigma_values[sym],
                    horizon_days=horizon_days,
                    n_paths=n_paths,
                    rng=rng,
                )
            symbol_paths_list.append(paths)

        symbol_paths = np.stack(symbol_paths_list, axis=0)

        # Compute portfolio value
        weights_array = np.array([position_weights.get(sym, 0.0) for sym in symbols])
        normalised = symbol_paths / symbol_paths[:, :, 0:1]
        portfolio_paths = weights_array @ normalised

        return symbol_paths, portfolio_paths
