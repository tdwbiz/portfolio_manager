from django.db import models


class BrokerStatement(models.Model):
    SOURCE_CHOICES = [
        ('IB', 'Interactive Brokers'),
        ('RBC', 'RBC Brokerage'),
    ]

    source = models.CharField(max_length=2, choices=SOURCE_CHOICES)
    file_date = models.DateField()
    raw_filename = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.source} Statement - {self.file_date}"


class Transaction(models.Model):
    TYPE_CHOICES = [
        ('BUY', 'Buy'),
        ('SELL', 'Sell'),
        ('DIV', 'Dividend'),
        ('FEE', 'Fee'),
        ('TRANSFER', 'Transfer'),
    ]

    statement = models.ForeignKey(
        BrokerStatement,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    date = models.DateField()
    symbol = models.CharField(max_length=20)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    price = models.DecimalField(max_digits=12, decimal_places=4)
    commission = models.DecimalField(max_digits=12, decimal_places=4)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')

    def __str__(self):
        return f"{self.type} {self.quantity} {self.symbol} on {self.date}"
