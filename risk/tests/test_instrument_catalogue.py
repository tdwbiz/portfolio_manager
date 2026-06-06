"""
Tests for M3-Spec-1: Instrument & Strategy Catalogue

Verifies:
  - Seed data integrity (8 instrument types, 11 strategy templates)
  - Instrument-template link correctness
  - Catalogue query filtering logic (eligibility, urgency, regime, etc.)
  - Seed idempotency (running twice does not create duplicates)

Run with:
    python manage.py test risk.tests.test_instrument_catalogue --settings=portfolio_manager.settings_test
"""

from django.test import TestCase
from django.core.management import call_command
from risk.models import InstrumentType, StrategyTemplate, StrategyInstrumentLink
from risk.services.catalogue import get_eligible_templates


class SeedDataIntegrityTests(TestCase):
    """Verify seed data is correctly populated and linked."""

    def setUp(self):
        call_command('seed_instrument_catalogue', verbosity=0)

    def test_all_eight_instrument_types_seeded(self):
        expected_codes = {
            'protective_put', 'collar', 'covered_call', 'trailing_stop',
            'inverse_etf', 'sector_rotation', 'partial_liquidation', 'cash_raise',
        }
        actual_codes = set(InstrumentType.objects.values_list('code', flat=True))
        self.assertEqual(actual_codes, expected_codes)

    def test_all_eleven_strategy_templates_seeded(self):
        count = StrategyTemplate.objects.filter(is_active=True).count()
        self.assertGreaterEqual(count, 11)

    def test_every_template_has_at_least_one_instrument(self):
        for template in StrategyTemplate.objects.filter(is_active=True):
            link_count = StrategyInstrumentLink.objects.filter(strategy=template).count()
            self.assertGreater(
                link_count,
                0,
                msg=f"Template '{template.code}' has no instrument links",
            )

    def test_collar_has_two_instruments(self):
        collar = StrategyTemplate.objects.get(code='collar_high_gain_taxable_stock')
        links = StrategyInstrumentLink.objects.filter(strategy=collar)
        self.assertEqual(links.count(), 2)
        codes = set(links.values_list('instrument__code', flat=True))
        self.assertIn('protective_put', codes)
        self.assertIn('covered_call', codes)

    def test_cef_template_has_cef_eligible_true(self):
        cef_template = StrategyTemplate.objects.get(code='trailing_stop_cef')
        self.assertTrue(cef_template.cef_eligible)

    def test_options_instruments_not_eligible_in_registered_accounts(self):
        protective_put = InstrumentType.objects.get(code='protective_put')
        self.assertFalse(protective_put.eligible_tfsa)
        self.assertFalse(protective_put.eligible_rrsp)
        self.assertFalse(protective_put.eligible_lif)
        self.assertFalse(protective_put.eligible_rrif)
        self.assertTrue(protective_put.eligible_taxable)

    def test_partial_liquidation_not_eligible_in_lif(self):
        partial_liq = InstrumentType.objects.get(code='partial_liquidation')
        self.assertFalse(partial_liq.eligible_lif)

    def test_trailing_stop_not_eligible_in_lif(self):
        trailing = InstrumentType.objects.get(code='trailing_stop')
        self.assertFalse(trailing.eligible_lif)

    def test_seed_is_idempotent(self):
        # Running seed twice should not create duplicates
        call_command('seed_instrument_catalogue', verbosity=0)
        call_command('seed_instrument_catalogue', verbosity=0)
        self.assertEqual(InstrumentType.objects.count(), 8)
        self.assertGreaterEqual(StrategyTemplate.objects.count(), 11)


class CatalogueQueryTests(TestCase):
    """Verify get_eligible_templates filtering logic."""

    def setUp(self):
        call_command('seed_instrument_catalogue', verbosity=0)

    def test_aapl_ibsusan_high_urgency_returns_collar(self):
        # AAPL in IB_SUSAN: common_stock, taxable, High urgency, options available
        templates = get_eligible_templates(
            asset_class='common_stock',
            account_category='Investment',
            urgency_level='High',
            regime_quadrant='Q2',
            options_available=True,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=196000,
        )
        codes = [t.code for t in templates]
        self.assertIn('collar_high_gain_taxable_stock', codes)
        self.assertIn('protective_put_pretax_stock', codes)

    def test_adx_liftom_returns_only_trailing_stop(self):
        # ADX in LIF_TOM: CEF, LIF (locked-in), no options
        templates = get_eligible_templates(
            asset_class='cef',
            account_category='LIF',
            urgency_level='High',
            regime_quadrant='Q3',
            options_available=False,
            is_cef=True,
            is_lif_or_rrif=True,
            unrealised_gain_usd=7890,
        )
        codes = [t.code for t in templates]
        # Only trailing_stop_cef should appear — it's the only CEF+LIF-eligible template
        self.assertIn('trailing_stop_cef', codes)
        # Collar and partial_liquidation must NOT appear
        self.assertNotIn('collar_high_gain_taxable_stock', codes)
        self.assertNotIn('partial_liquidation_taxable', codes)

    def test_ibit_tfsa_critical_urgency_returns_trailing_stop(self):
        # IBIT in TFSA: crypto_etf, TFSA, Critical urgency, no options
        templates = get_eligible_templates(
            asset_class='crypto_etf',
            account_category='TFSA',
            urgency_level='Critical',
            regime_quadrant='Q3',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        codes = [t.code for t in templates]
        self.assertIn('trailing_stop_registered', codes)
        # Options-based strategies must NOT appear (TFSA not eligible)
        self.assertNotIn('collar_high_gain_taxable_stock', codes)
        self.assertNotIn('protective_put_pretax_stock', codes)

    def test_taxable_partial_liquidation_only_at_critical(self):
        # The taxable partial_liquidation template requires Critical urgency
        medium_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='Medium',
            regime_quadrant='Q1',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=100000,
        )
        critical_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='Critical',
            regime_quadrant='Q1',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=100000,
        )
        medium_codes = [t.code for t in medium_templates]
        critical_codes = [t.code for t in critical_templates]
        self.assertNotIn('partial_liquidation_taxable', medium_codes)
        self.assertIn('partial_liquidation_taxable', critical_codes)

    def test_inverse_etf_templates_only_in_q3_q4(self):
        q1_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='TFSA',
            urgency_level='High',
            regime_quadrant='Q1',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        q3_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='TFSA',
            urgency_level='High',
            regime_quadrant='Q3',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        q1_codes = [t.code for t in q1_templates]
        q3_codes = [t.code for t in q3_templates]
        self.assertNotIn('inverse_etf_semiconductor', q1_codes)
        self.assertNotIn('inverse_etf_nasdaq', q1_codes)
        self.assertIn('inverse_etf_semiconductor', q3_codes)
        self.assertIn('inverse_etf_nasdaq', q3_codes)

    def test_sector_rotation_only_in_q3_q4(self):
        q1_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='High',
            regime_quadrant='Q1',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        q3_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='High',
            regime_quadrant='Q3',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        q1_codes = [t.code for t in q1_templates]
        q3_codes = [t.code for t in q3_templates]
        self.assertNotIn('sector_rotation_defensive', q1_codes)
        self.assertIn('sector_rotation_defensive', q3_codes)

    def test_covered_call_income_only_at_low_medium_urgency(self):
        high_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='High',
            regime_quadrant='Q1',
            options_available=True,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=10000,
        )
        low_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='Low',
            regime_quadrant='Q1',
            options_available=True,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=10000,
        )
        high_codes = [t.code for t in high_templates]
        low_codes = [t.code for t in low_templates]
        self.assertNotIn('covered_call_income', high_codes)
        self.assertIn('covered_call_income', low_codes)

    def test_collar_requires_min_unrealised_gain(self):
        # collar requires min_unrealised_gain_usd=50000
        small_gain_templates = get_eligible_templates(
            asset_class='common_stock',
            account_category='Investment',
            urgency_level='High',
            regime_quadrant='Q2',
            options_available=True,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=10000,
        )
        large_gain_templates = get_eligible_templates(
            asset_class='common_stock',
            account_category='Investment',
            urgency_level='High',
            regime_quadrant='Q2',
            options_available=True,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=100000,
        )
        small_codes = [t.code for t in small_gain_templates]
        large_codes = [t.code for t in large_gain_templates]
        self.assertNotIn('collar_high_gain_taxable_stock', small_codes)
        self.assertIn('collar_high_gain_taxable_stock', large_codes)
