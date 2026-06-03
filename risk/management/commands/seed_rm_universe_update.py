"""
Management command: seed_rm_universe_update

Add new symbols (BANK, CAGE, DRAM) to rm_security_universe and their
index exposures.

Usage:
    python manage.py seed_rm_universe_update
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from risk.models import SecurityIndexExposure, SecurityUniverse

logger = logging.getLogger(__name__)

# New symbols from June 2, 2026 portfolio export
NEW_SYMBOLS = [
    {
        'symbol': 'BANK',
        'name': 'BMO Equal Weight Banks Index ETF',
        'security_type': 'etf',
        'asset_class': 'etf_equity',
        'sector': 'Financial',
        'primary_currency': 'CAD',
        'is_held': True,
        'is_benchmark': False,
        'is_hedge': False,
        'is_cef': False,
        'use_jump_diffusion': False,
        'options_available': True,
        'amibroker_symbol': 'BANK.TO',
        'is_active': True,
        'notes': 'Canadian banks ETF; listed on TSX as BANK.TO',
    },
    {
        'symbol': 'CAGE',
        'name': 'CI Global Asset Allocation ETF',
        'security_type': 'etf',
        'asset_class': 'etf_equity',
        'sector': 'Multi-asset',
        'primary_currency': 'CAD',
        'is_held': True,
        'is_benchmark': False,
        'is_hedge': False,
        'is_cef': False,
        'use_jump_diffusion': False,
        'options_available': True,
        'amibroker_symbol': 'CAGE.TO',
        'is_active': True,
        'notes': 'Listed on TSX; verify full name against yFinance',
    },
    {
        'symbol': 'DRAM',
        'name': 'VanEck Semiconductor ETF (Memory)',
        'security_type': 'etf',
        'asset_class': 'etf_equity',
        'sector': 'Technology',
        'primary_currency': 'USD',
        'is_held': True,
        'is_benchmark': False,
        'is_hedge': False,
        'is_cef': False,
        'use_jump_diffusion': False,
        'options_available': True,
        'amibroker_symbol': 'DRAM',
        'is_active': True,
        'notes': 'US-listed; semiconductor/memory focus',
    },
]

NEW_INDEX_EXPOSURES = {
    'BANK': [
        ('DIVIDEND_CA', 0.90, 'Canadian dividend index exposure'),
        ('TSX', 1.00, 'TSX index exposure'),
    ],
    'DRAM': [
        ('SEMICONDUCTOR', 0.80, 'Semiconductor index exposure'),
        ('TECHNOLOGY', 0.20, 'Technology index exposure'),
    ],
    'CAGE': [
        ('BROAD_MARKET', 1.00, 'Broad market index exposure (placeholder)'),
    ],
}


class Command(BaseCommand):
    help = 'Add new symbols (BANK, CAGE, DRAM) to rm_security_universe.'

    def handle(self, *args, **options):
        created = 0
        updated = 0

        for fields in NEW_SYMBOLS:
            symbol = fields['symbol']
            defaults = {k: v for k, v in fields.items() if k != 'symbol'}
            obj, is_created = SecurityUniverse.objects.update_or_create(
                symbol=symbol,
                defaults=defaults,
            )
            if is_created:
                created += 1
                self.stdout.write(
                    self.style.SUCCESS(f'Created: {symbol} - {obj.name}')
                )
            else:
                updated += 1
                self.stdout.write(
                    self.style.WARNING(f'Updated: {symbol} - {obj.name}')
                )

        # Seed index exposures
        for symbol, exposures in NEW_INDEX_EXPOSURES.items():
            for index_name, weight, notes in exposures:
                SecurityIndexExposure.objects.update_or_create(
                    symbol=symbol,
                    index_name=index_name,
                    defaults={
                        'exposure_weight': weight,
                        'notes': notes,
                    },
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Index exposure: {symbol} → {index_name} ({weight})'
                    )
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Seed Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Symbols created: {created}')
        self.stdout.write(f'  Symbols updated: {updated}')
        self.stdout.write(self.style.SUCCESS('=' * 50))
