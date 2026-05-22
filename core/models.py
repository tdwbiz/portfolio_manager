from django.db import models
from decimal import Decimal
import hashlib


def compute_tx_hash(broker: str, account_id: str, date, symbol: str,
                    tx_type: str, quantity, price, amount, currency: str) -> str:
    """Return a SHA-256 hex digest that uniquely identifies a transaction.

    All numeric fields are normalised to fixed decimal places so that the
    same trade imported from two different overlapping export files always
    produces the same hash.
    """
    key = "|".join([
        broker or '',
        account_id or '',
        str(date),
        symbol or '',
        tx_type or '',
        f"{Decimal(str(quantity)):.4f}",
        f"{Decimal(str(price)):.4f}",
        f"{Decimal(str(amount)):.2f}",
        currency or '',
    ])
    return hashlib.sha256(key.encode()).hexdigest()


class TransactionType(models.Model):
    """Lookup table for transaction types.

    New types are auto-created on import so no code change is required
    when a broker introduces a new activity type.
    """
    code             = models.CharField(max_length=20, unique=True)
    label            = models.CharField(max_length=100)
    affects_position = models.BooleanField(default=False, help_text='True for BUY / SELL — changes share count')
    affects_cash     = models.BooleanField(default=True,  help_text='True when the amount column represents a cash flow')

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.label}"


class Account(models.Model):
    BROKER_CHOICES = [('IB', 'Interactive Brokers'), ('RBC', 'RBC Brokerage')]
    CATEGORY_CHOICES = [
        ('RRSP',    'RRSP (Retirement)'),
        ('LIRA',    'LIRA (Locked-In Retirement)'),
        ('TFSA',    'TFSA (Tax-Free)'),
        ('TAXABLE', 'Taxable'),
        ('RESP',    'RESP (Education)'),
        ('OTHER',   'Other'),
    ]
    account_id         = models.CharField(max_length=50, unique=True)   # e.g. "TOM IB", "TS JOINT", "RBC-123456"
    name               = models.CharField(max_length=100)               # human display name, editable
    broker             = models.CharField(max_length=3, choices=BROKER_CHOICES)
    currency           = models.CharField(max_length=3, default='USD')
    category           = models.CharField(
        max_length=10,
        choices=CATEGORY_CHOICES,
        default='TAXABLE',
        help_text='Account tax category (RRSP, TFSA, Taxable, etc.)'
    )
    owner              = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text='Person or entity this account belongs to (e.g. "Tom", "Wife", "Joint")'
    )
    starting_balance       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    starting_balance_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.broker})"


class BrokerStatement(models.Model):
    SOURCE_CHOICES = [
        ('IB', 'Interactive Brokers'),
        ('RBC', 'RBC Brokerage'),
    ]

    source = models.CharField(max_length=3, choices=SOURCE_CHOICES)
    file_date = models.DateField()
    raw_filename = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.source} Statement - {self.file_date}"


class Transaction(models.Model):
    statement = models.ForeignKey(
        BrokerStatement,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    account = models.ForeignKey(
        'Account',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='transactions'
    )
    date = models.DateField()
    symbol = models.CharField(max_length=50, blank=True, default='')
    type = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    price = models.DecimalField(max_digits=12, decimal_places=4)
    commission = models.DecimalField(max_digits=12, decimal_places=4)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    exchange_rate_cad = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal('1.000000'))
    amount_cad = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, default='')
    tx_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)

    def __str__(self):
        return f"{self.type} {self.quantity} {self.symbol} on {self.date}"


class FileImportLog(models.Model):
    filename = models.CharField(max_length=255)
    broker = models.CharField(max_length=3)  # IB or RBC
    imported_at = models.DateTimeField(auto_now_add=True)
    records_imported = models.IntegerField(default=0)
    records_skipped = models.IntegerField(default=0)
    status = models.CharField(max_length=20, default='success')  # success / partial / error
    error_message = models.TextField(blank=True, default='')
    content_hash = models.CharField(max_length=64, blank=True, default='')

    def __str__(self):
        return f"{self.filename} - {self.status}"


class BrandingConfig(models.Model):
    """Singleton model for application branding and theme configuration."""
    company_name = models.CharField(max_length=100, default='Portfolio Manager', help_text='Company or application name')
    company_logo_url = models.URLField(blank=True, default='', help_text='URL to company logo image')
    tagline = models.CharField(max_length=255, blank=True, default='', help_text='Company tagline or slogan')
    
    # Color scheme - Django Admin inspired colors
    primary_color = models.CharField(max_length=7, default='#417690', help_text='Primary brand color (hex)')
    secondary_color = models.CharField(max_length=7, default='#447e9b', help_text='Secondary color (hex)')
    success_color = models.CharField(max_length=7, default='#28a745', help_text='Success/positive color (hex)')
    danger_color = models.CharField(max_length=7, default='#ba2121', help_text='Danger/negative color (hex)')
    navbar_bg_color = models.CharField(max_length=7, default='#417690', help_text='Navigation bar background (hex)')
    navbar_text_color = models.CharField(max_length=7, default='#ffffff', help_text='Navigation bar text (hex)')
    
    # Footer
    footer_text = models.CharField(max_length=255, blank=True, default='', help_text='Footer copyright text')
    footer_bg_color = models.CharField(max_length=7, default='#f8f9fa', help_text='Footer background (hex)')
    
    # Features
    show_logo = models.BooleanField(default=True, help_text='Display company logo in navbar')
    show_tagline = models.BooleanField(default=True, help_text='Display tagline')
    
    class Meta:
        verbose_name = 'Branding Configuration'
        verbose_name_plural = 'Branding Configuration'
    
    def __str__(self):
        return f"Branding: {self.company_name}"
    
    def save(self, *args, **kwargs):
        """Ensure only one instance exists (singleton pattern)."""
        self.pk = 1
        super().save(*args, **kwargs)
    
    @classmethod
    def load(cls):
        """Load the singleton instance, create if not exists."""
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class HistoricalPrice(models.Model):
    """Daily closing prices for portfolio symbols and FX rates.

    Populated by the fetch_historical_prices management command.
    Used by compute_portfolio_value_series to avoid live yfinance calls.
    """
    symbol = models.CharField(max_length=20)
    date = models.DateField()
    close = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        unique_together = [('symbol', 'date')]
        indexes = [
            models.Index(fields=['symbol', 'date']),
        ]
        ordering = ['symbol', 'date']

    def __str__(self):
        return f"{self.symbol} {self.date}: {self.close}"
