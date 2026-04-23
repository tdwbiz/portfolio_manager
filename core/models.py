from django.db import models


class BrokerStatement(models.Model):
    SOURCE_CHOICES = [
        ('IB', 'Interactive Brokers'),
        ('RBC', 'RBC Investments'),
    ]

    source = models.CharField(max_length=3, choices=SOURCE_CHOICES)
    file_date = models.DateField()
    raw_filename = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.source} Statement ({self.file_date})"

    class Meta:
        ordering = ['-file_date']


class Transaction(models.Model):
    TYPE_CHOICES = [
        ('BUY', 'Buy'),
        ('SELL', 'Sell'),
        ('DIV', 'Dividend'),
        ('INT', 'Interest'),
        ('FEE', 'Fee'),
    ]

    statement = models.ForeignKey(
        BrokerStatement,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    date = models.DateField()
    symbol = models.CharField(max_length=20)
    type = models.CharField(max_length=4, choices=TYPE_CHOICES)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    price = models.DecimalField(max_digits=12, decimal_places=4)
    commission = models.DecimalField(max_digits=12, decimal_places=4)
    amount = models.DecimalField(max_digits=14, decimal_places=4)
    currency = models.CharField(max_length=3, default='USD')

    def __str__(self):
        return f"{self.symbol} {self.type} ({self.date})"

    class Meta:
        ordering = ['-date']
