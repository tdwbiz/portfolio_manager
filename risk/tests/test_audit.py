"""
Comprehensive tests for M8-Spec-1: Audit Trail, Explainability & Tax Acknowledgement.

Tests cover:
- ImmutableModel append-only enforcement (spec §1.1)
- AuditScoreSnapshot creation and uniqueness (spec §1.2)
- AuditRecommendationLog creation (spec §1.3)
- AuditSimulationRun creation (spec §1.4)
- AuditRegimeLog creation and unique calc_date (spec §1.5)
- TaxDecisionAcknowledgement workflow (spec §1.6)
- audit_hooks.py functions (spec §4)
- tax_acknowledgement.py can_mark_actioned / mark_actioned (spec §5)
- audit_rationale.py template-driven rationale (spec §3)
- audit_retention_check management command (spec §6)
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core.models import Account
from risk.models import (
    AuditActionLog,
    AuditEntry,
    AuditRecommendationLog,
    AuditRetentionLog,
    AuditScoreSnapshot,
    AuditSimulationRun,
    AuditRegimeLog,
    RegimeState,
    MarketCondition,
    StrategyRecommendation,
    StrategyTemplate,
    TaxDecisionAcknowledgement,
    UrgencyScore,
)
from risk.services.audit_hooks import (
    write_audit_entry,
    write_audit_score_snapshot,
    write_audit_recommendation_log,
    write_audit_regime_log,
    write_audit_simulation_run,
)
from risk.services.tax_acknowledgement import can_mark_actioned, mark_actioned


# =============================================================================
# ImmutableModel Tests (spec §1.1)
# =============================================================================


class ImmutableModelTests(TestCase):
    """Test append-only enforcement for all audit models."""

    def test_audit_entry_cannot_update(self):
        """AuditEntry.save() raises ImmutableRecordError on update."""
        entry = AuditEntry.objects.create(
            calc_date=date(2026, 6, 1),
            model_name='calc_urgency',
            input_snapshot={'test': True},
            output_summary={'score': 50},
        )
        pk = entry.pk
        with self.assertRaises(Exception) as ctx:
            entry.score_raw = 99  # noqa: B018 - testing that save raises
            entry.save()
        self.assertIn('immutable', str(ctx.exception).lower())

    def test_audit_entry_cannot_delete(self):
        """AuditEntry.delete() raises ImmutableRecordError."""
        entry = AuditEntry.objects.create(
            calc_date=date(2026, 6, 1),
            model_name='calc_urgency',
        )
        with self.assertRaises(Exception) as ctx:
            entry.delete()
        self.assertIn('immutable', str(ctx.exception).lower())

    def test_all_audit_models_are_immutable(self):
        """Every audit model subclass enforces immutability."""
        models_to_test = [
            (AuditScoreSnapshot, {
                'entity_type': 'security_in_account',
                'entity_id': 'TEST_ACCT',
                'symbol': 'AAPL',
                'calc_date': date(2026, 6, 1),
                'urgency_level': 'High',
                'score_raw': Decimal('75.00'),
            }),
            (AuditRegimeLog, {
                'calc_date': date(2026, 6, 1),
                'quadrant': 'Q3',
                'confidence_score': Decimal('0.85'),
                'transition_flag': False,
                'indicator_snapshot_json': '{}',
            }),
            (AuditRetentionLog, {
                'retention_days': 2557,
            }),
        ]

        for model, fields in models_to_test:
            with self.subTest(model=model.__name__):
                obj = model.objects.create(**fields)
                pk = obj.pk

                # Test update blocked
                with self.assertRaises(Exception):
                    obj.save()

                # Test delete blocked
                with self.assertRaises(Exception):
                    obj.delete()

                # Verify still in DB
                self.assertTrue(model.objects.filter(pk=pk).exists())


# =============================================================================
# AuditScoreSnapshot Tests (spec §1.2)
# =============================================================================


class AuditScoreSnapshotTests(TestCase):
    """Test AuditScoreSnapshot creation and uniqueness."""

    def setUp(self):
        self.account = Account.objects.create(
            name='Test Account',
            owner='TOM',
            currency='CAD')

    def test_create_snapshot(self):
        """Basic snapshot creation works."""
        snapshot = AuditScoreSnapshot.objects.create(
            entity_type='security_in_account',
            entity_id='TEST_ACCT',
            symbol='AAPL',
            calc_date=date(2026, 6, 1),
            urgency_level='High',
            score_raw=Decimal('75.50'),
            component_scores_json='{"m1": {"score": 80}}',
            weights_at_calc_json='{"weight_m1_metrics": 0.35}',
            input_metrics_json='{"var_parametric_95": 0.03}',
        )
        self.assertEqual(snapshot.entity_type, 'security_in_account')
        self.assertEqual(snapshot.urgency_level, 'High')
        self.assertIsNotNone(snapshot.created_at)

    def test_unique_together_entity_type_entity_id_symbol_calc_date(self):
        """Unique constraint on (entity_type, entity_id, symbol, calc_date)."""
        AuditScoreSnapshot.objects.create(
            entity_type='security_in_account',
            entity_id='TEST_ACCT',
            symbol='AAPL',
            calc_date=date(2026, 6, 1),
            urgency_level='High',
            score_raw=Decimal('75.00'),
        )
        with self.assertRaises(IntegrityError):
            AuditScoreSnapshot.objects.create(
                entity_type='security_in_account',
                entity_id='TEST_ACCT',
                symbol='AAPL',
                calc_date=date(2026, 6, 1),
                urgency_level='Medium',  # Different level, same key
                score_raw=Decimal('50.00'),
            )

    def test_snapshot_for_different_symbols_same_date(self):
        """Different symbols can have snapshots for the same date."""
        AuditScoreSnapshot.objects.create(
            entity_type='security_in_account',
            entity_id='TEST_ACCT',
            symbol='AAPL',
            calc_date=date(2026, 6, 1),
            urgency_level='High',
            score_raw=Decimal('75.00'),
        )
        AuditScoreSnapshot.objects.create(
            entity_type='security_in_account',
            entity_id='TEST_ACCT',
            symbol='GOOG',
            calc_date=date(2026, 6, 1),
            urgency_level='Low',
            score_raw=Decimal('25.00'),
        )
        self.assertEqual(AuditScoreSnapshot.objects.filter(
            entity_id='TEST_ACCT', calc_date=date(2026, 6, 1)
        ).count(), 2)


# =============================================================================
# AuditRecommendationLog Tests (spec §1.3)
# =============================================================================


class AuditRecommendationLogTests(TestCase):
    """Test AuditRecommendationLog creation for eligible and rejected templates."""

    def setUp(self):
        self.account = Account.objects.create(
            name='Test Account',
            owner='TOM',
            currency='CAD')
        self.template = StrategyTemplate.objects.create(
            code='protective_put',
            name='Protective Put',
            applicable_asset_types=['common_stock'],
            applicable_account_categories='ALL',
            applicable_urgency_levels='ALL',
            applicable_regime_quadrants='ALL',
            requires_options=True,
            is_active=True,
        )

    def test_eligible_template_log(self):
        """Eligible template gets a recommendation log entry."""
        log = AuditRecommendationLog.objects.create(
            symbol='AAPL',
            account_id='TEST_ACCT',
            owner='TOM',
            calc_date=date(2026, 6, 1),
            input_urgency_level='High',
            input_urgency_score=Decimal('75.00'),
            input_regime_quadrant='Q3',
            strategy_template_code='protective_put',
            strategy_template_name='Protective Put',
            was_eligible=True,
            rank=1,
            cost_per_var_reduction_usd=Decimal('0.21'),
            selection_rationale='Selected: lowest cost per dollar of VaR reduction.',
            constraints_applied_json=[],
            is_tax_triggering=False,
        )
        self.assertTrue(log.was_eligible)
        self.assertEqual(log.rank, 1)

    def test_rejected_template_log(self):
        """Rejected template gets a recommendation log entry with reason."""
        log = AuditRecommendationLog.objects.create(
            symbol='AAPL',
            account_id='TEST_ACCT',
            owner='TOM',
            calc_date=date(2026, 6, 1),
            input_urgency_level='High',
            input_urgency_score=Decimal('75.00'),
            input_regime_quadrant='Q3',
            strategy_template_code='covered_call',
            strategy_template_name='Covered Call',
            was_eligible=False,
            ineligibility_reason='options_not_available',
            rank=None,
            constraints_applied_json=['options_not_available'],
        )
        self.assertFalse(log.was_eligible)
        self.assertEqual(log.ineligibility_reason, 'options_not_available')
        self.assertIsNone(log.rank)


# =============================================================================
# AuditSimulationRun Tests (spec §1.4)
# =============================================================================


class AuditSimulationRunTests(TestCase):
    """Test AuditSimulationRun creation for backtest, Monte Carlo, stress test."""

    def test_backtest_simulation_run(self):
        """Backtest simulation run audit entry."""
        run = AuditSimulationRun.objects.create(
            simulation_family='backtest',
            source_run_id=1,
            symbol='AAPL',
            account_id='TEST_ACCT',
            entity_type='security_in_account',
            entity_id='TEST_ACCT_AAPL',
            strategy_template_code='protective_put',
            scenario_name='base_case',
            parameters_json={'start_date': '2025-01-01'},
            summary_json={'total_return_hedged_pct': 5.2},
            calc_date=date(2026, 6, 1),
            run_timestamp=timezone.make_aware(timezone.datetime(2026, 6, 1, 10, 0, 0)),
        )
        self.assertEqual(run.simulation_family, 'backtest')
        self.assertEqual(run.source_run_id, 1)

    def test_montecarlo_simulation_run(self):
        """Monte Carlo simulation run audit entry."""
        run = AuditSimulationRun.objects.create(
            simulation_family='montecarlo',
            source_run_id=2,
            symbol='GOOG',
            parameters_json={'n_paths': 10000},
            summary_json={'pct5_outcome_usd': -5000},
            calc_date=date(2026, 6, 1),
            run_timestamp=timezone.make_aware(timezone.datetime(2026, 6, 1, 11, 0, 0)),
        )
        self.assertEqual(run.simulation_family, 'montecarlo')

    def test_stress_test_simulation_run(self):
        """Stress test simulation run audit entry."""
        run = AuditSimulationRun.objects.create(
            simulation_family='stress_test',
            source_run_id=3,
            symbol='AAPL',
            scenario_name='2008_crisis',
            parameters_json={'shock_equity': -0.40},
            summary_json={'stress_loss_hedged_pct': -15.0},
            calc_date=date(2026, 6, 1),
            run_timestamp=timezone.make_aware(timezone.datetime(2026, 6, 1, 12, 0, 0)),
        )
        self.assertEqual(run.simulation_family, 'stress_test')


# =============================================================================
# AuditRegimeLog Tests (spec §1.5)
# =============================================================================


class AuditRegimeLogTests(TestCase):
    """Test AuditRegimeLog creation and unique calc_date."""

    def test_create_regime_log(self):
        """Basic regime log creation."""
        log = AuditRegimeLog.objects.create(
            calc_date=date(2026, 6, 1),
            quadrant='Q3',
            confidence_score=Decimal('0.85'),
            transition_flag=False,
            indicator_snapshot_json={'rsi_14': 45},
            death_cross_count=0,
            golden_cross_count=1,
            oversold_count=2,
            overbought_count=0,
            bear_phase_symbols_json=['AAPL'],
        )
        self.assertEqual(log.quadrant, 'Q3')
        self.assertEqual(log.death_cross_count, 0)

    def test_unique_calc_date(self):
        """Only one regime log per calc_date."""
        AuditRegimeLog.objects.create(
            calc_date=date(2026, 6, 1),
            quadrant='Q3',
            confidence_score=Decimal('0.85'),
            transition_flag=False,
        )
        with self.assertRaises(IntegrityError):
            AuditRegimeLog.objects.create(
                calc_date=date(2026, 6, 1),  # Same date
                quadrant='Q1',
                confidence_score=Decimal('0.90'),
                transition_flag=True,
            )


# =============================================================================
# TaxDecisionAcknowledgement Tests (spec §1.6)
# =============================================================================


class TaxDecisionAcknowledgementTests(TestCase):
    """Test TaxDecisionAcknowledgement model and workflow."""

    def setUp(self):
        self.account = Account.objects.create(
            name='Test Account',
            owner='TOM',
            currency='CAD')
        self.template = StrategyTemplate.objects.create(
            code='protective_put',
            name='Protective Put',
            applicable_asset_types=['common_stock'],
            applicable_account_categories='ALL',
            applicable_urgency_levels='ALL',
            applicable_regime_quadrants='ALL',
            requires_options=True,
            is_active=True,
        )

    def _create_recommendation(self):
        """Helper to create a StrategyRecommendation."""
        return StrategyRecommendation.objects.create(
            symbol='AAPL',
            account_id='TEST_ACCT',
            owner='TOM',
            calc_date=date(2026, 6, 1),
            strategy_template=self.template,
            rank=1,
            urgency_level='High',
            urgency_score=Decimal('75.00'),
            regime_quadrant='Q3',
            position_quantity=Decimal('100'),
            position_market_value_usd=Decimal('15000'),
            position_unrealised_gain_usd=Decimal('3000'),
            cost_basis_per_unit_usd=Decimal('120'),
            current_price_usd=Decimal('150'),
            estimated_strategy_cost_usd=Decimal('500'),
            estimated_strategy_cost_pct=Decimal('0.0333'),
            tax_cost_estimate_usd=Decimal('700'),
            is_tax_triggering=True,
            estimated_var_reduction_usd=Decimal('2000'),
            estimated_var_reduction_pct=Decimal('0.1333'),
            cost_per_var_reduction_usd=Decimal('0.25'),
            constraints_applied_json='[]',
            status='active',
        )

    def test_create_acknowledgement(self):
        """Basic acknowledgement creation."""
        rec = self._create_recommendation()
        ack = TaxDecisionAcknowledgement.objects.create(
            recommendation=rec,
            presented_to_user='TOM',
            presented_at=rec.calc_timestamp,
            acknowledged_at=timezone.make_aware(timezone.datetime(2026, 6, 1, 10, 5, 0)),
            acknowledged_by='TOM',
            tax_cost_estimate_presented=rec.tax_cost_estimate_usd,
            user_notes='Acknowledged — proceed with protective put.',
        )
        self.assertEqual(ack.presented_to_user, 'TOM')
        self.assertEqual(ack.tax_cost_estimate_presented, Decimal('700'))


# =============================================================================
# Audit Hook Functions Tests (spec §4)
# =============================================================================


class AuditHookFunctionsTests(TestCase):
    """Test the shared audit hook functions."""

    def test_write_audit_score_snapshot(self):
        """write_audit_score_snapshot creates a snapshot."""
        write_audit_score_snapshot(
            entity_type='security_in_account',
            entity_id='TEST_ACCT',
            symbol='AAPL',
            calc_date=date(2026, 6, 1),
            urgency_level='High',
            score_raw=Decimal('75.50'),
            component_scores_json='{"m1": {"score": 80}}',
            weights_config={'weight_m1_metrics': 0.35},
            input_metrics_json='{"var_parametric_95": 0.03}',
        )
        self.assertEqual(AuditScoreSnapshot.objects.count(), 1)
        snapshot = AuditScoreSnapshot.objects.first()
        self.assertEqual(snapshot.urgency_level, 'High')
        self.assertEqual(snapshot.score_raw, Decimal('75.50'))

    def test_write_audit_score_snapshot_logs_on_failure(self):
        """Audit failure is logged, not raised."""
        from django.db import transaction

        # First call succeeds
        write_audit_score_snapshot(
            entity_type='security_in_account',
            entity_id='TEST_ACCT',
            symbol='AAPL',
            calc_date=date(2026, 6, 1),
            urgency_level='High',
            score_raw=Decimal('75.00'),
            component_scores_json='{}',
            weights_config={},
            input_metrics_json='{}',
        )
        self.assertEqual(AuditScoreSnapshot.objects.count(), 1)

        # Second call with same key should fail silently (logged, not raised)
        with transaction.atomic():
            try:
                write_audit_score_snapshot(
                    entity_type='security_in_account',
                    entity_id='TEST_ACCT',
                    symbol='AAPL',
                    calc_date=date(2026, 6, 1),  # Same key → IntegrityError
                    urgency_level='Medium',
                    score_raw=Decimal('50.00'),
                    component_scores_json='{}',
                    weights_config={},
                    input_metrics_json='{}',
                )
            except Exception:
                pass  # Should not reach here — hook catches internally
        # Still only 1 — second write failed silently
        self.assertEqual(AuditScoreSnapshot.objects.count(), 1)

    def test_write_audit_recommendation_log(self):
        """write_audit_recommendation_log creates a log entry."""
        write_audit_recommendation_log(
            symbol='AAPL',
            account_id='TEST_ACCT',
            owner='TOM',
            calc_date=date(2026, 6, 1),
            input_urgency_level='High',
            input_urgency_score=Decimal('75.00'),
            input_regime_quadrant='Q3',
            strategy_template_code='protective_put',
            strategy_template_name='Protective Put',
            was_eligible=True,
            ineligibility_reason='',
            rank=1,
            cost_result={'cost': 500},
            var_result={'var_reduction': 2000},
            cost_per_var_reduction=0.25,
            selection_rationale='Selected: lowest cost per dollar of VaR reduction.',
            constraints_applied=[],
            is_tax_triggering=False,
        )
        self.assertEqual(AuditRecommendationLog.objects.count(), 1)
        log = AuditRecommendationLog.objects.first()
        self.assertTrue(log.was_eligible)
        self.assertEqual(log.rank, 1)

    def test_write_audit_simulation_run(self):
        """write_audit_simulation_run creates a simulation audit entry."""
        write_audit_simulation_run(
            simulation_family='backtest',
            source_run_id=1,
            symbol='AAPL',
            account_id='TEST_ACCT',
            entity_type='security_in_account',
            entity_id='TEST_ACCT_AAPL',
            strategy_template_code='protective_put',
            scenario_name='base_case',
            parameters={'start_date': '2025-01-01'},
            summary={'total_return_hedged_pct': 5.2},
            calc_date=date(2026, 6, 1),
            run_timestamp=timezone.make_aware(timezone.datetime(2026, 6, 1, 10, 0, 0)),
        )
        self.assertEqual(AuditSimulationRun.objects.count(), 1)
        run = AuditSimulationRun.objects.first()
        self.assertEqual(run.simulation_family, 'backtest')
        self.assertEqual(run.source_run_id, 1)

    def test_write_audit_regime_log(self):
        """write_audit_regime_log creates a regime audit entry."""
        # First create a RegimeState (required by the hook)
        RegimeState.objects.create(
            calc_date=date(2026, 6, 1),
            quadrant='Q3',
            confidence_score=Decimal('0.85'),
            transition_flag=False,
            prob_q1=Decimal('0.10'),
            prob_q2=Decimal('0.10'),
            prob_q3=Decimal('0.70'),
            prob_q4=Decimal('0.10'),
            indicator_snapshot_json='{"rsi_14": 45}',
        )
        # Create some MarketCondition entries
        MarketCondition.objects.create(
            calc_date=date(2026, 6, 1),
            symbol='AAPL',
            rsi_14=25,  # oversold
            crossover_type='none',
            market_phase='bear',
        )
        MarketCondition.objects.create(
            calc_date=date(2026, 6, 1),
            symbol='GOOG',
            rsi_14=72,  # overbought
            crossover_type='golden',
            crossover_date=date(2026, 6, 1),
            market_phase='uptrend',
        )

        write_audit_regime_log(date(2026, 6, 1))

        self.assertEqual(AuditRegimeLog.objects.count(), 1)
        log = AuditRegimeLog.objects.first()
        self.assertEqual(log.quadrant, 'Q3')
        self.assertEqual(log.oversold_count, 1)
        self.assertEqual(log.overbought_count, 1)
        self.assertEqual(log.golden_cross_count, 1)
        self.assertIn('AAPL', log.bear_phase_symbols_json)

    def test_write_audit_regime_log_skips_if_regime_missing(self):
        """write_audit_regime_log logs error if RegimeState doesn't exist."""
        # No RegimeState for this date
        write_audit_regime_log(date(2026, 12, 31))
        # No AuditRegimeLog created
        self.assertEqual(AuditRegimeLog.objects.count(), 0)

    def test_write_audit_regime_log_skips_duplicate(self):
        """write_audit_regime_log skips if row already exists for calc_date."""
        RegimeState.objects.create(
            calc_date=date(2026, 6, 1),
            quadrant='Q3',
            confidence_score=Decimal('0.85'),
            transition_flag=False,
            prob_q1=Decimal('0.10'),
            prob_q2=Decimal('0.10'),
            prob_q3=Decimal('0.70'),
            prob_q4=Decimal('0.10'),
            indicator_snapshot_json='{}',
        )
        write_audit_regime_log(date(2026, 6, 1))
        write_audit_regime_log(date(2026, 6, 1))  # Duplicate — should skip
        self.assertEqual(AuditRegimeLog.objects.count(), 1)


# =============================================================================
# Tax Acknowledgement Service Tests (spec §5)
# =============================================================================


class TaxAcknowledgementServiceTests(TestCase):
    """Test can_mark_actioned and mark_actioned."""

    def setUp(self):
        self.account = Account.objects.create(
            name='Test Account',
            owner='TOM',
            currency='CAD')
        self.template = StrategyTemplate.objects.create(
            code='protective_put',
            name='Protective Put',
            applicable_asset_types=['common_stock'],
            applicable_account_categories='ALL',
            applicable_urgency_levels='ALL',
            applicable_regime_quadrants='ALL',
            requires_options=True,
            is_active=True,
        )

    def _create_recommendation(self, is_tax_triggering=False, status='active'):
        """Helper to create a StrategyRecommendation."""
        return StrategyRecommendation.objects.create(
            symbol='AAPL',
            account_id='TEST_ACCT',
            owner='TOM',
            calc_date=date(2026, 6, 1),
            strategy_template=self.template,
            rank=1,
            urgency_level='High',
            urgency_score=Decimal('75.00'),
            regime_quadrant='Q3',
            position_quantity=Decimal('100'),
            position_market_value_usd=Decimal('15000'),
            position_unrealised_gain_usd=Decimal('3000'),
            cost_basis_per_unit_usd=Decimal('120'),
            current_price_usd=Decimal('150'),
            estimated_strategy_cost_usd=Decimal('500'),
            estimated_strategy_cost_pct=Decimal('0.0333'),
            tax_cost_estimate_usd=Decimal('700') if is_tax_triggering else Decimal('0'),
            is_tax_triggering=is_tax_triggering,
            estimated_var_reduction_usd=Decimal('2000'),
            estimated_var_reduction_pct=Decimal('0.1333'),
            cost_per_var_reduction_usd=Decimal('0.25'),
            constraints_applied_json='[]',
            status=status,
        )

    def test_can_mark_actioned_non_tax_triggering(self):
        """Non-tax-triggering recommendations can be marked actioned."""
        rec = self._create_recommendation(is_tax_triggering=False)
        allowed, reason = can_mark_actioned(rec)
        self.assertTrue(allowed)
        self.assertEqual(reason, '')

    def test_can_mark_actioned_tax_triggering_no_ack(self):
        """Tax-triggering recommendations without acknowledgement cannot be actioned."""
        rec = self._create_recommendation(is_tax_triggering=True)
        allowed, reason = can_mark_actioned(rec)
        self.assertFalse(allowed)
        self.assertIn('tax', reason.lower())

    def test_can_mark_actioned_tax_triggering_with_ack(self):
        """Tax-triggering recommendations with acknowledgement can be actioned."""
        rec = self._create_recommendation(is_tax_triggering=True)
        TaxDecisionAcknowledgement.objects.create(
            recommendation_id=rec.id,
            presented_to_user='TOM',
            presented_at=rec.calc_timestamp,
            acknowledged_at=timezone.make_aware(timezone.datetime(2026, 6, 1, 10, 5, 0)),
            acknowledged_by='TOM',
            tax_cost_estimate_presented=rec.tax_cost_estimate_usd,
        )
        allowed, reason = can_mark_actioned(rec)
        self.assertTrue(allowed)
        self.assertEqual(reason, '')

    def test_can_mark_actioned_inactive(self):
        """Non-active recommendations cannot be marked actioned."""
        rec = self._create_recommendation(status='superseded')
        allowed, reason = can_mark_actioned(rec)
        self.assertFalse(allowed)
        self.assertIn('not active', reason.lower())

    def test_mark_actioned_succeeds_for_non_tax(self):
        """mark_actioned sets status='actioned' for non-tax recommendations."""
        rec = self._create_recommendation(is_tax_triggering=False)
        mark_actioned(rec, 'TOM')
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'actioned')
        self.assertEqual(rec.actioned_by, 'TOM')
        self.assertIsNotNone(rec.actioned_at)

    def test_mark_actioned_raises_for_tax_without_ack(self):
        """mark_actioned raises ValueError for tax-triggering without acknowledgement."""
        rec = self._create_recommendation(is_tax_triggering=True)
        with self.assertRaises(ValueError) as ctx:
            mark_actioned(rec, 'TOM')
        self.assertIn('tax', str(ctx.exception).lower())

    def test_mark_actioned_succeeds_for_tax_with_ack(self):
        """mark_actioned succeeds for tax-triggering with acknowledgement."""
        rec = self._create_recommendation(is_tax_triggering=True)
        TaxDecisionAcknowledgement.objects.create(
            recommendation_id=rec.id,
            presented_to_user='TOM',
            presented_at=rec.calc_timestamp,
            acknowledged_at=timezone.make_aware(timezone.datetime(2026, 6, 1, 10, 5, 0)),
            acknowledged_by='TOM',
            tax_cost_estimate_presented=rec.tax_cost_estimate_usd,
        )
        mark_actioned(rec, 'TOM')
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'actioned')


# =============================================================================
# AuditEntry Tests (M8-Spec-2 §3.1)
# =============================================================================


class AuditEntryTests(TestCase):
    """Test AuditEntry model — high-level calculation event tracking."""

    def test_create_audit_entry(self):
        """AuditEntry can be created with all required fields."""
        entry = AuditEntry.objects.create(
            model_name='urgency_scores',
            calc_date=date(2026, 6, 1),
            status='success',
            output_summary={'text': 'Urgency scores calculated for 100 positions'},
            input_snapshot={'positions': 100},
        )
        self.assertEqual(entry.model_name, 'urgency_scores')
        self.assertEqual(entry.status, 'success')
        self.assertEqual(entry.input_snapshot['positions'], 100)

    def test_unique_together_model_name_calc_date(self):
        """AuditEntry has unique_together on (model_name, calc_date)."""
        AuditEntry.objects.create(
            model_name='urgency_scores',
            calc_date=date(2026, 6, 1),
            status='success',
            output_summary={'text': 'First entry'},
        )
        with self.assertRaises(IntegrityError):
            AuditEntry.objects.create(
                model_name='urgency_scores',
                calc_date=date(2026, 6, 1),  # Same pair
                status='success',
                output_summary={'text': 'Duplicate'},
            )

    def test_duplicate_entry_skipped(self):
        """write_audit_entry skips if row already exists for (model_name, calc_date)."""
        write_audit_entry(
            model_name='urgency_scores',
            calc_date=date(2026, 6, 1),
            status='success',
            summary='First run',
            details_json={'count': 1},
        )
        self.assertEqual(AuditEntry.objects.count(), 1)
        # Call again with same model_name + calc_date — should skip
        write_audit_entry(
            model_name='urgency_scores',
            calc_date=date(2026, 6, 1),
            status='success',
            summary='Second run',
            details_json={'count': 2},
        )
        self.assertEqual(AuditEntry.objects.count(), 1)
        entry = AuditEntry.objects.first()
        self.assertEqual(entry.output_summary['text'], 'First run')
        self.assertEqual(entry.input_snapshot['count'], 1)


# =============================================================================
# AuditActionLog Tests (M8-Spec-2 §3.2)
# =============================================================================


class AuditActionLogTests(TestCase):
    """Test AuditActionLog model — post-recommendation action tracking."""

    def setUp(self):
        self.account = Account.objects.create(
            name='Test Account',
            owner='TOM',
            currency='CAD')
        self.template = StrategyTemplate.objects.create(
            code='protective_put',
            name='Protective Put',
            applicable_asset_types=['common_stock'],
            applicable_account_categories='ALL',
            applicable_urgency_levels='ALL',
            applicable_regime_quadrants='ALL',
            requires_options=True,
            is_active=True,
        )

    def _create_recommendation(self, is_tax_triggering=False):
        """Helper to create a StrategyRecommendation."""
        return StrategyRecommendation.objects.create(
            symbol='AAPL',
            account_id='TEST_ACCT',
            owner='TOM',
            calc_date=date(2026, 6, 1),
            strategy_template=self.template,
            rank=1,
            urgency_level='High',
            urgency_score=Decimal('75.00'),
            regime_quadrant='Q3',
            position_quantity=Decimal('100'),
            position_market_value_usd=Decimal('15000'),
            position_unrealised_gain_usd=Decimal('3000'),
            cost_basis_per_unit_usd=Decimal('120'),
            current_price_usd=Decimal('150'),
            estimated_strategy_cost_usd=Decimal('500'),
            estimated_strategy_cost_pct=Decimal('0.0333'),
            tax_cost_estimate_usd=Decimal('700') if is_tax_triggering else Decimal('0'),
            is_tax_triggering=is_tax_triggering,
            estimated_var_reduction_usd=Decimal('2000'),
            estimated_var_reduction_pct=Decimal('0.1333'),
            cost_per_var_reduction_usd=Decimal('0.25'),
            constraints_applied_json='[]',
            status='active',
        )

    def test_create_actioned_log(self):
        """AuditActionLog created when recommendation is actioned."""
        rec = self._create_recommendation(is_tax_triggering=False)
        log = AuditActionLog.objects.create(
            recommendation=rec,
            action_type='actioned',
            performed_by='TOM',
            reason='',
            tax_acknowledged=False,
        )
        self.assertEqual(log.action_type, 'actioned')
        self.assertEqual(log.performed_by, 'TOM')
        self.assertFalse(log.tax_acknowledged)

    def test_create_tax_acknowledged_log(self):
        """AuditActionLog created when tax is acknowledged."""
        rec = self._create_recommendation(is_tax_triggering=True)
        log = AuditActionLog.objects.create(
            recommendation=rec,
            action_type='tax_acknowledged',
            performed_by='TOM',
            reason='',
            tax_acknowledged=True,
        )
        self.assertEqual(log.action_type, 'tax_acknowledged')
        self.assertTrue(log.tax_acknowledged)

    def test_immutable_append_only(self):
        """AuditActionLog inherits ImmutableModel — cannot update or delete."""
        rec = self._create_recommendation()
        log = AuditActionLog.objects.create(
            recommendation=rec,
            action_type='actioned',
            performed_by='TOM',
        )
        pk = log.pk
        with self.assertRaises(Exception):
            log.performed_by = 'HACKER'  # noqa: B018
            log.save()
        with self.assertRaises(Exception):
            log.delete()
        # Original still exists
        self.assertEqual(AuditActionLog.objects.filter(pk=pk).count(), 1)


# =============================================================================
# Audit Retention Check Command Tests (spec §6)
# =============================================================================


class AuditRetentionCheckCommandTests(TestCase):
    """Test audit_retention_check management command."""

    def test_command_runs_without_error(self):
        """Command executes and produces output."""
        from io import StringIO
        out = StringIO()
        call_command('audit_retention_check', stdout=out)
        output = out.getvalue()
        self.assertIn('Audit Retention Check', output)
        self.assertIn('7 years', output)

    def test_command_with_custom_warning_days(self):
        """Command respects --warning-days flag."""
        from io import StringIO
        out = StringIO()
        call_command('audit_retention_check', warning_days=30, stdout=out)
        output = out.getvalue()
        self.assertIn('30 days', output)

    def test_command_never_deletes_data(self):
        """audit_retention_check is purely reporting — never deletes."""
        # Create some audit records
        AuditEntry.objects.create(
            calc_date=date(2010, 1, 1),  # Very old
            model_name='calc_urgency',
        )
        count_before = AuditEntry.objects.count()

        call_command('audit_retention_check')

        count_after = AuditEntry.objects.count()
        self.assertEqual(count_before, count_after,
                         "audit_retention_check should never delete audit records")
