"""
Seed historical scenarios for backtesting.

Creates 4 standard historical scenarios:
1. 2022 Rate Shock — Bank of Canada 425bp rate hikes
2. 2020 COVID Crash — March 2020 pandemic selloff
3. 2008 Global Financial Crisis — Lehman Brothers collapse
4. 2024 Tech Correction — AI bubble correction

Idempotent: deletes existing scenarios and recreates on each run.

Usage:
    python manage.py seed_historical_scenarios --settings=portfolio_manager.settings_dev
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction

from risk.models import HistoricalScenario


SCENARIOS = [
    {
        'name': '2022_rate_shock',
        'display_name': '2022 Rate Shock (Bank of Canada 425bp hikes)',
        'start_date': '2022-01-03',
        'end_date': '2022-12-30',
        'description': (
            'Bank of Canada raised its policy rate from 0.25% to 4.75% over 2022, '
            'the fastest tightening cycle in decades. Canadian equities (S&P/TSX) fell '
            '~18%, US equities (S&P 500) fell ~19%. Bonds were hit hard — the FTSE '
            'Canada Universe Bond Index fell ~12%. Energy and materials held up better '
            'than growth stocks. Real estate dropped sharply on rate sensitivity.'
        ),
        'asset_class_returns': {
            'equity_ca': -0.18,
            'equity_us': -0.19,
            'fixed_income_ca': -0.12,
            'commodities': 0.08,
            'real_estate': -0.22,
            'cash': 0.045,
        },
        'benchmark_spy_return': -0.194,
        'benchmark_qqq_return': -0.325,
        'benchmark_vdy_return': -0.168,
    },
    {
        'name': '2020_covid_crash',
        'display_name': '2020 COVID Crash (March 2020 pandemic selloff)',
        'start_date': '2020-02-19',
        'end_date': '2020-03-23',
        'description': (
            'Global pandemic outbreak triggered the fastest crash in history. The S&P 500 '
            'lost ~34% in 23 trading days. Canadian markets fell ~30%. Oil turned negative. '
            'Safe-haven flows boosted gold and bonds. Tech survived better than energy. '
            'Recovery was V-shaped — markets bottomed in March and recovered by August.'
        ),
        'asset_class_returns': {
            'equity_ca': -0.30,
            'equity_us': -0.34,
            'fixed_income_ca': 0.02,
            'commodities': -0.55,
            'real_estate': -0.25,
            'cash': 0.00,
        },
        'benchmark_spy_return': -0.337,
        'benchmark_qqq_return': -0.293,
        'benchmark_vdy_return': -0.295,
    },
    {
        'name': '2008_gfc',
        'display_name': '2008 Global Financial Crisis (Lehman Brothers collapse)',
        'start_date': '2007-10-01',
        'end_date': '2009-03-09',
        'description': (
            'The Global Financial Crisis triggered by the US subprime mortgage collapse. '
            'Lehman Brothers bankruptcy in September 2008 caused a global panic. The S&P 500 '
            'fell ~57% from peak to trough. Canadian markets fell ~45%. Credit markets froze. '
            'Only utilities and consumer staples provided any defense. Recovery took nearly '
            '4 years for US markets and 5+ years for Canadian markets.'
        ),
        'asset_class_returns': {
            'equity_ca': -0.45,
            'equity_us': -0.57,
            'fixed_income_ca': 0.05,
            'commodities': -0.54,
            'real_estate': -0.40,
            'cash': 0.01,
        },
        'benchmark_spy_return': -0.568,
        'benchmark_qqq_return': -0.742,
        'benchmark_vdy_return': -0.435,
    },
    {
        'name': '2024_tech_correction',
        'display_name': '2024 Tech Correction (AI bubble correction)',
        'start_date': '2024-07-01',
        'end_date': '2024-10-31',
        'description': (
            'AI-hyped tech stocks corrected sharply in Q3 2024 as investors rotated away '
            'from expensive growth names. The NASDAQ fell ~10% from its July peak. Canadian '
            'markets were more resilient due to energy and financials. Rate cut expectations '
            'in September/October helped markets recover by November. The correction was '
            'sharper in mega-cap tech than in diversified or value-oriented portfolios.'
        ),
        'asset_class_returns': {
            'equity_ca': -0.04,
            'equity_us': -0.06,
            'fixed_income_ca': 0.02,
            'commodities': -0.02,
            'real_estate': -0.08,
            'cash': 0.005,
        },
        'benchmark_spy_return': -0.045,
        'benchmark_qqq_return': -0.105,
        'benchmark_vdy_return': -0.035,
    },
]


class Command(BaseCommand):
    help = 'Seed 4 standard historical scenarios for backtesting.'

    def handle(self, *args, **options):
        verbosity = options.get('verbosity', 1)

        with transaction.atomic():
            # Delete existing scenarios for idempotency
            deleted_count = HistoricalScenario.objects.all().delete()[0]
            if verbosity >= 1:
                self.stdout.write(f'Cleared {deleted_count} existing scenarios.')

            created = []
            for s in SCENARIOS:
                HistoricalScenario.objects.create(
                    name=s['name'],
                    display_name=s['display_name'],
                    start_date=s['start_date'],
                    end_date=s['end_date'],
                    description=s['description'],
                    asset_class_returns_json=json.dumps(s['asset_class_returns']),
                    benchmark_spy_return=s['benchmark_spy_return'],
                    benchmark_qqq_return=s['benchmark_qqq_return'],
                    benchmark_vdy_return=s['benchmark_vdy_return'],
                    is_active=True,
                )
                created.append(s['name'])

            if verbosity >= 1:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Seeded {len(created)} scenarios: {", ".join(created)}'
                    )
                )
