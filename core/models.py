from django.db import models
from decimal import Decimal


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
    account_id    = models.CharField(max_length=50, unique=True)   # e.g. "TOM IB", "TS JOINT", "RBC-123456"
    name          = models.CharField(max_length=100)               # human display name, editable
    broker        = models.CharField(max_length=3, choices=BROKER_CHOICES)
    currency      = models.CharField(max_length=3, default='USD')
    starting_balance     = models.DecimalField(max_digits=15, decimal_places=2, default=0)
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
