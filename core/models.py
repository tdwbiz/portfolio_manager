import hashlib
import logging
from decimal import Decimal

from django.db import models


def compute_tx_hash(
    broker: str,
    account_id: str,
    date,
    symbol: str,
    tx_type: str,
    quantity,
    price,
    amount,
    currency: str,
) -> str:
    """Return a SHA-256 hex digest that uniquely identifies a transaction.

    All numeric fields are normalised to fixed decimal places so that the
    same trade imported from two different overlapping export files always
    produces the same hash.
    """
    key = "|".join(
        [
            broker or "",
            account_id or "",
            str(date),
            symbol or "",
            tx_type or "",
            f"{Decimal(str(quantity)):.4f}",
            f"{Decimal(str(price)):.4f}",
            f"{Decimal(str(amount)):.2f}",
            currency or "",
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()


class TransactionType(models.Model):
    """Lookup table for transaction types.

    New types are auto-created on import so no code change is required
    when a broker introduces a new activity type.
    """

    code = models.CharField(max_length=20, unique=True)
    label = models.CharField(max_length=100)
    affects_position = models.BooleanField(
        default=False, help_text="True for BUY / SELL — changes share count"
    )
    affects_cash = models.BooleanField(
        default=True, help_text="True when the amount column represents a cash flow"
    )

    class Meta:
        ordering = ["code"]

    @classmethod
    def auto_create_or_get(cls, code: str, label: str) -> "TransactionType":
        """Get or create a TransactionType record.

        Args:
            code: Canonical code (e.g., 'BUY', 'SELL', 'DIV')
            label: Human-readable label (e.g., 'Buy', 'Sell', 'Dividend')

        Returns:
            TransactionType instance
        """
        obj, created = cls.objects.get_or_create(code=code, defaults={"label": label})
        if created:
            logger = logging.getLogger(__name__)
            logger.info(f"Auto-created TransactionType: {code} — {label}")
        return obj

    def __str__(self):
        return f"{self.code} — {self.label}"


class AccountCurrency(models.Model):
    """Sub-account representing one currency within an Account.

    An Account (e.g. 'TOM ADVISOR') may have multiple sub-accounts
    for different currencies (CAD, USD). Each sub-account has its own
    starting balance and tracks per-currency statements.

    This aligns the Account model with how the rest of the system already
    handles multi-currency: CoreMonthlyStatement, CoreAnnualReport,
    RbcMonthly, RbcAnnual, and Transaction all have their own `currency`
    field alongside the account FK.
    """

    CURRENCY_CHOICES = [("CAD", "CAD"), ("USD", "USD")]

    account = models.ForeignKey(
        "Account",
        on_delete=models.CASCADE,
        related_name="currencies",
    )
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
    )
    starting_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    starting_balance_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = [("account", "currency")]
        ordering = ["currency"]

    def __str__(self):
        return f"{self.account.account_id} ({self.currency})"


class Account(models.Model):
    BROKER_CHOICES = [("IB", "Interactive Brokers"), ("RBC", "RBC Brokerage")]
    CATEGORY_CHOICES = [
        ("RRSP", "RRSP (Retirement)"),
        ("LIRA", "Locked-In Retirement"),
        ("TFSA", "Tax-Free"),
        ("TAXABLE", "Taxable"),
        ("RESP", "RESP (Education)"),
        ("OTHER", "Other"),
    ]
    account_id = models.CharField(
        max_length=50, unique=True
    )  # e.g. "TOM IB", "TS JOINT", "RBC-123456"
    name = models.CharField(max_length=100)  # human display name, editable
    broker = models.CharField(max_length=3, choices=BROKER_CHOICES)
    # DEPRECATED: Use AccountCurrency instead. Kept for backward compat.
    # After migration, this reflects the dominant currency or 'MIX' for multi-currency.
    # Note: max_length=3 due to existing database column constraint.
    currency = models.CharField(
        max_length=3,
        default="CAD",
        help_text="DEPRECATED: Use AccountCurrency sub-accounts instead. "
        "Auto-set to dominant currency after migration.",
    )
    category = models.CharField(
        max_length=10,
        choices=CATEGORY_CHOICES,
        default="TAXABLE",
        help_text="Account tax category (RRSP, TFSA, Taxable, etc.)",
    )
    owner = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text='Person or entity this account belongs to (e.g. "Tom", "Wife", "Joint")',
    )
    # DEPRECATED: Use AccountCurrency.starting_balance instead.
    starting_balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="DEPRECATED: Use AccountCurrency sub-accounts instead.",
    )
    starting_balance_date = models.DateField(
        null=True,
        blank=True,
        help_text="DEPRECATED: Use AccountCurrency sub-accounts instead.",
    )
    exclude_from_risk = models.BooleanField(
        default=False,
        help_text="When True, this account is excluded from risk calculations",
    )

    class Meta:
        indexes = [
            models.Index(fields=["broker", "category"]),
            models.Index(fields=["owner"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.broker})"

    @property
    def currency_labels(self):
        """Return list of currency labels for this account."""
        return list(self.currencies.values_list("currency", flat=True))

    @property
    def has_multi_currency(self):
        """True if this account has more than one currency sub-account."""
        return self.currencies.count() > 1


class PositionHistory(models.Model):
    """Tracks historical positions for an account over time."""

    owner = models.CharField(max_length=50)
    account = models.ForeignKey(
        "Account", on_delete=models.PROTECT, related_name="position_history"
    )
    symbol = models.CharField(max_length=50)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    cost_basis_per_unit = models.DecimalField(max_digits=12, decimal_places=4)
    total_cost_basis = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="CAD")
    date_from = models.DateField()
    date_to = models.DateField(null=True, blank=True)
    as_of_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_position_history"
        indexes = [
            models.Index(fields=["account", "symbol", "date_from", "date_to"]),
            models.Index(fields=["owner", "date_from", "date_to"]),
            models.Index(fields=["symbol", "date_from", "date_to"]),
        ]

    def __str__(self):
        return f"{self.owner} - {self.symbol} - {self.date_from}"


class BrokerStatement(models.Model):
    SOURCE_CHOICES = [
        ("IB", "Interactive Brokers"),
        ("RBC", "RBC Brokerage"),
    ]

    source = models.CharField(max_length=3, choices=SOURCE_CHOICES)
    file_date = models.DateField()
    raw_filename = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.source} Statement - {self.file_date}"


class Transaction(models.Model):
    statement = models.ForeignKey(
        BrokerStatement, on_delete=models.CASCADE, related_name="transactions"
    )
    account = models.ForeignKey(
        "Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    date = models.DateField()
    symbol = models.CharField(max_length=200, blank=True, default="")
    type = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    price = models.DecimalField(max_digits=12, decimal_places=4)
    commission = models.DecimalField(max_digits=12, decimal_places=4)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    exchange_rate_cad = models.DecimalField(
        max_digits=10, decimal_places=6, default=Decimal("1.000000")
    )
    amount_cad = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    notes = models.TextField(blank=True, default="")
    tx_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="SHA-256 hash for deduplication",
    )

    class Meta:
        indexes = [
            models.Index(fields=["account", "date"]),
            models.Index(fields=["symbol", "date"]),
            models.Index(fields=["type"]),
        ]

    def __str__(self):
        return f"{self.type} {self.quantity} {self.symbol} on {self.date}"


class RbcMonthly(models.Model):
    """A parsed RBC PDF monthly statement — one row per currency section per file.

    RBC PDFs often contain both CAD and USD holdings. Each currency
    section is stored as a separate row so they can be queried independently.
    Renamed from PdfStatement in migration 0016.
    """

    CURRENCY_CHOICES = [("CAD", "CAD"), ("USD", "USD")]

    account = models.ForeignKey(
        "Account",
        on_delete=models.CASCADE,
        related_name="rbc_monthly_statements",
    )
    file_name = models.CharField(max_length=255)
    statement_date = models.DateField()
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="CAD",
        help_text="Currency of this statement section (CAD or USD)",
    )
    last_statement_date = models.DateField(
        null=True, blank=True, help_text="Date of previous statement"
    )
    account_number = models.CharField(
        max_length=50, blank=True, default="", help_text="RBC account number from PDF"
    )
    fx_rate = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("1.000000"),
        help_text="USD to CAD exchange rate as of statement date",
    )
    begin_total_market_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total portfolio market value at the previous statement date (BEGINNING)",
    )
    end_total_market_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total portfolio market value at this statement date (ENDING)",
    )
    parsed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-statement_date", "currency"]
        unique_together = [("account", "statement_date", "currency", "file_name")]

    def __str__(self):
        return f"RBC Monthly {self.account} — {self.statement_date} [{self.currency}]"


class RbcMonthlyBalance(models.Model):
    """Asset summary balances from an RBC monthly statement.

    One row per asset category (Cash, Mutual Funds, Common Shares, etc.) per statement.
    Renamed from PdfAccountBalance in migration 0016.
    """

    rbc_monthly = models.ForeignKey(
        RbcMonthly,
        on_delete=models.CASCADE,
        related_name="balances",
    )
    currency = models.CharField(
        max_length=3,
        default="CAD",
        help_text="Currency of this balance row (CAD or USD)",
    )
    account_type = models.CharField(
        max_length=50,
        help_text='e.g. "Cash", "Mutual Funds", "Foreign Securities", "Common Shares"',
    )
    opening_balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Opening balance at last statement date",
    )
    closing_balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Closing balance at this statement date",
    )
    market_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Market value at this statement date",
    )
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Percentage of total portfolio",
    )

    class Meta:
        ordering = ["account_type"]
        unique_together = [("rbc_monthly", "account_type")]

    def __str__(self):
        return f"{self.rbc_monthly} — {self.account_type}: ${self.market_value}"


class RbcMonthlyTransaction(models.Model):
    """Individual transaction from an RBC monthly PDF statement.

    Renamed from PdfTransaction in migration 0016.
    """

    rbc_monthly = models.ForeignKey(
        RbcMonthly,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    date = models.DateField()
    activity_type = models.CharField(
        max_length=100, help_text='e.g. "INTEREST", "BUY", "SELL", "DIVIDEND"'
    )
    description = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Full activity description from PDF",
    )
    symbol = models.CharField(max_length=200, blank=True, default="")
    quantity = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000")
    )
    price = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000")
    )
    debit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount debited (outflow)",
    )
    credit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount credited (inflow)",
    )
    currency = models.CharField(max_length=3, default="CAD")
    commission = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.00")
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["date", "id"]
        indexes = [
            models.Index(fields=["rbc_monthly", "date"]),
        ]

    def __str__(self):
        return f"{self.activity_type} {self.date} — {self.description[:50]}"


class RbcMonthlyHolding(models.Model):
    """Each individual security/holding from an RBC PDF Asset Review table.

    One row per security per statement — captures quantity, book cost,
    price, and market value for each ticker.
    Renamed from PdfHolding in migration 0026.
    """

    rbc_monthly = models.ForeignKey(
        RbcMonthly,
        on_delete=models.CASCADE,
        related_name="holdings",
    )
    currency = models.CharField(max_length=3, default="CAD")
    security = models.CharField(max_length=200, help_text="Full security name")
    symbol = models.CharField(max_length=200, blank=True, default="", help_text="Ticker symbol")
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    book_cost = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    market_price = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    market_value = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["symbol"]

    def __str__(self):
        sym = f" [{self.symbol}]" if self.symbol else ""
        return f"{self.rbc_monthly} — {self.security}{sym}: {self.market_value}"


class FileImportLog(models.Model):
    filename = models.CharField(max_length=255)
    broker = models.CharField(max_length=3)  # IB or RBC
    imported_at = models.DateTimeField(auto_now_add=True)
    records_imported = models.IntegerField(default=0)
    records_skipped = models.IntegerField(default=0)
    status = models.CharField(
        max_length=20, default="success"
    )  # success / partial / error
    error_message = models.TextField(blank=True, default="")
    content_hash = models.CharField(max_length=64, blank=True, default="")

    def __str__(self):
        return f"{self.filename} - {self.status}"


class BrandingConfig(models.Model):
    """Singleton model for application branding and theme configuration."""

    company_name = models.CharField(
        max_length=100,
        default="Portfolio Manager",
        help_text="Company or application name",
    )
    company_logo_url = models.URLField(
        blank=True, default="", help_text="URL to company logo image"
    )
    tagline = models.CharField(
        max_length=255, blank=True, default="", help_text="Company tagline or slogan"
    )

    # Color scheme - Django Admin inspired colors
    primary_color = models.CharField(
        max_length=7, default="#417690", help_text="Primary brand color (hex)"
    )
    secondary_color = models.CharField(
        max_length=7, default="#447e9b", help_text="Secondary color (hex)"
    )
    success_color = models.CharField(
        max_length=7, default="#28a745", help_text="Success/positive color (hex)"
    )
    danger_color = models.CharField(
        max_length=7, default="#ba2121", help_text="Danger/negative color (hex)"
    )
    navbar_bg_color = models.CharField(
        max_length=7, default="#417690", help_text="Navigation bar background (hex)"
    )
    navbar_text_color = models.CharField(
        max_length=7, default="#ffffff", help_text="Navigation bar text (hex)"
    )

    # Footer
    footer_text = models.CharField(
        max_length=255, blank=True, default="", help_text="Footer copyright text"
    )
    footer_bg_color = models.CharField(
        max_length=7, default="#f8f9fa", help_text="Footer background (hex)"
    )

    # Features
    show_logo = models.BooleanField(
        default=True, help_text="Display company logo in navbar"
    )
    show_tagline = models.BooleanField(default=True, help_text="Display tagline")

    class Meta:
        verbose_name = "Branding Configuration"
        verbose_name_plural = "Branding Configuration"

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


class RbcAnnual(models.Model):
    """Flat table for RBC Annual Investment Performance Report data.

    One row per (account, report_year, currency). Both CAD and USD sections
    of the same PDF produce separate rows so currency totals are unambiguous.

    Replaces the six-table normalised hierarchy (RbcAnnualReport,
    RbcAnnualBalances, RbcAnnualPerformance, RbcAnnualPerformanceReturn,
    RbcAnnualCharges, RbcAnnualCompensation) from migration 0015.
    Created in migration 0017.

    Cross-check:
      bal_ending_value == RbcMonthly.total_market_value for Dec of same year
      bal_deposits_in  ~= sum(RbcMonthlyTransaction.credit) for DEP/TFR-IN same year
    """

    CURRENCY_CHOICES = [("CAD", "CAD"), ("USD", "USD")]

    # --- Header ---
    account = models.ForeignKey(
        "Account",
        on_delete=models.CASCADE,
        related_name="rbc_annual_records",
    )
    file_name = models.CharField(max_length=255)
    report_year = models.IntegerField(
        help_text="Calendar year this report covers, e.g. 2017"
    )
    report_period_start = models.DateField(
        help_text="First day of period, e.g. 2017-01-01"
    )
    report_period_end = models.DateField(
        help_text="Last day of period, e.g. 2017-12-31"
    )
    account_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="RBC account number from PDF, e.g. 900-00005",
    )
    account_type_code = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="RRSP, RRIF, RIF, LIF, LIRA, TFSA, RESP",
    )
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="CAD",
        help_text="Currency section this row describes",
    )
    parsed_at = models.DateTimeField(auto_now_add=True)

    # --- Balances ("Change in account value") ---
    bal_beginning_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Market value at start of period",
    )
    bal_deposits_in = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total deposits and transfers-in",
    )
    bal_withdrawals_out = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total withdrawals and transfers-out (stored as positive)",
    )
    bal_net_return = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Net investment return for the period",
    )
    bal_ending_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Market value at end of period — cross-check vs Dec monthly",
    )
    bal_amount_invested = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Beginning value + deposits - withdrawals",
    )

    # --- Performance ("Money-weighted rate of return") ---
    perf_since_date = models.DateField(
        null=True, blank=True, help_text="Reference date for since-inception return"
    )
    perf_1yr = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Past 1 year return %",
    )
    perf_3yr = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Past 3 year annualised return %",
    )
    perf_5yr = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Past 5 year annualised return %",
    )
    perf_10yr = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Past 10 year annualised return %",
    )
    perf_since = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Return since perf_since_date %",
    )

    # --- Charges ---
    chg_operating = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Operating charges",
    )
    chg_commissions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Commissions — Equities/options",
    )
    chg_total_transaction = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total transaction charges subtotal",
    )
    chg_total_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total paid by you",
    )

    # --- Compensation (third-party) ---
    comp_trailing = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Trailing commissions received by RBC",
    )
    comp_other = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Other third-party compensation",
    )
    comp_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total compensation from third parties",
    )

    class Meta:
        ordering = ["-report_year", "account", "currency"]
        unique_together = [("account", "report_year", "currency")]

    def __str__(self):
        return f"RBC Annual {self.account} — {self.report_year} [{self.currency}]"


class HistoricalPrice(models.Model):
    """Daily closing prices for portfolio symbols and FX rates.

    Populated by the fetch_historical_prices management command.
    Used by compute_portfolio_value_series to avoid live yfinance calls.
    """

    symbol = models.CharField(max_length=20)
    date = models.DateField()
    close = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        unique_together = [("symbol", "date")]
        indexes = [
            models.Index(fields=["symbol", "date"]),
        ]
        ordering = ["symbol", "date"]

    def __str__(self):
        return f"{self.symbol} {self.date}: {self.close}"


class DividendSchedule(models.Model):
    """Manually tracked expected dividend/income payments.

    Used for cash flow planning — shows upcoming dividend, distribution,
    and interest payments for portfolio symbols.
    """

    symbol = models.CharField(max_length=50)
    pay_date = models.DateField()
    amount_per_share = models.DecimalField(max_digits=10, decimal_places=6)
    currency = models.CharField(max_length=3, default="USD")
    exchange_rate_cad = models.DecimalField(
        max_digits=10, decimal_places=6, default=Decimal("1.0")
    )
    type = models.CharField(
        max_length=10,
        choices=[("DIV", "Dividend"), ("DIST", "Distribution"), ("INT", "Interest")],
        default="DIV",
    )
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["pay_date", "symbol"]
        unique_together = [("symbol", "pay_date", "type")]

    def __str__(self):
        type_map = dict(self._meta.get_field("type").choices)
        type_label = type_map.get(self.type, self.type)
        return f"{self.symbol} - {self.pay_date.strftime('%Y-%m-%d')} ({type_label})"

    def amount_cad_per_share(self) -> Decimal:
        """Return amount per share in CAD."""
        return self.amount_per_share * self.exchange_rate_cad


class FixedPriceConfig(models.Model):
    """Configurable fixed prices for securities that don't have market prices.

    Used for closed-end funds, trusts, or other instruments where yfinance
    cannot fetch prices. Prices are set manually and cached.
    """

    symbol = models.CharField(max_length=50, unique=True)
    price_cad = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    price_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    currency = models.CharField(max_length=3, default="CAD")
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_fixed_price_config"
        ordering = ["symbol"]

    def __str__(self):
        return f"{self.symbol} ({self.currency})"

    @property
    def active_price(self):
        """Return the price in the configured currency."""
        if self.currency == "USD":
            return self.price_usd
        return self.price_cad


class CoreMonthlyStatement(models.Model):
    """Monthly statement generated from core_transaction data.

    Replaces RBC monthly statement data when CSV is available and complete.
    One row per (account, month, currency).
    """

    CURRENCY_CHOICES = [("CAD", "CAD"), ("USD", "USD")]

    account = models.ForeignKey(
        "Account",
        on_delete=models.CASCADE,
        related_name="core_monthly_statements",
    )
    statement_month = models.DateField(help_text="First day of the month")
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="CAD",
    )

    # Portfolio value metrics
    beginning_market_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    ending_market_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )

    # Transaction totals for the month
    total_buys = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_sells = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_dividends = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_interest = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_commission = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )

    # Derived
    net_flow = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_transactions = models.IntegerField(default=0)
    ending_cash_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="Cash balance at end of statement month (per-currency)")

    # Tracking
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_monthly_statement"
        unique_together = [("account", "statement_month", "currency")]
        ordering = ["-statement_month"]
        indexes = [
            models.Index(fields=["account", "statement_month"]),
        ]

    def __str__(self):
        return f"{self.account} — {self.statement_month.strftime('%Y-%m')} [{self.currency}]"


class CoreAnnualReport(models.Model):
    """Annual report generated from core_transaction data.

    Replaces RBC annual report data when CSV is available and complete.
    One row per (account, year, currency).
    """

    CURRENCY_CHOICES = [("CAD", "CAD"), ("USD", "USD")]

    account = models.ForeignKey(
        "Account",
        on_delete=models.CASCADE,
        related_name="core_annual_reports",
    )
    report_year = models.IntegerField(help_text="Calendar year")
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="CAD",
    )

    # Balance metrics
    beginning_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    ending_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    deposits = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    withdrawals = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    net_investment_return = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    amount_invested = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )

    # Summary totals
    total_buys = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_sells = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_commission = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_transactions = models.IntegerField(default=0)

    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_annual_report"
        unique_together = [("account", "report_year", "currency")]
        ordering = ["-report_year"]
        indexes = [
            models.Index(fields=["account", "report_year"]),
        ]

    def __str__(self):
        return f"{self.account} — {self.report_year} [{self.currency}]"


class CoreReconciliation(models.Model):
    """Reconciliation result between CSV-generated and RBC PDF data.

    Tracks matches and discrepancies for each account/month/year.
    """

    TYPE_CHOICES = [("monthly", "Monthly"), ("annual", "Annual")]

    account = models.ForeignKey(
        "Account",
        on_delete=models.CASCADE,
        related_name="core_reconciliations",
    )
    reconciliation_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
    )
    period_start = models.DateField(help_text="Start of period")
    period_end = models.DateField(help_text="End of period")

    # CSV values
    csv_beginning_value = models.DecimalField(
        max_digits=15, decimal_places=2, null=True
    )
    csv_ending_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_buys = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_sells = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_income = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_transactions = models.IntegerField(null=True)

    # RBC values
    rbc_beginning_value = models.DecimalField(
        max_digits=15, decimal_places=2, null=True
    )
    rbc_ending_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_buys = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_sells = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_income = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_transactions = models.IntegerField(null=True)

    # Discrepancy tracking
    matching = models.BooleanField(default=False)
    discrepancy_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    discrepancy_notes = models.TextField(blank=True, default="")

    reconciled_at = models.DateTimeField(auto_now_add=True)
    reconciled_by = models.CharField(max_length=100, blank=True, default="auto")

    class Meta:
        db_table = "core_reconciliation"
        unique_together = [
            ("account", "reconciliation_type", "period_start", "period_end")
        ]
        ordering = ["-period_start"]
        indexes = [
            models.Index(fields=["account", "reconciliation_type"]),
        ]

    def __str__(self):
        return f"{self.account} {self.reconciliation_type} {self.period_start}"
