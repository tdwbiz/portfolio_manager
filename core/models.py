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
    currency: str,
    amount=None,
    settlement_date=None,
    seq=1,
) -> str:
    """Return a SHA-256 hex digest that uniquely identifies a transaction.

    The hash uses only fields present in both RBC export layouts (9- and
    11-column) — broker, account, date, settlement date, activity/type,
    symbol, quantity, price, value and currency — never Symbol Description
    or Description. All numeric fields are normalised to fixed decimal
    places so that the same trade imported from two different overlapping
    export files always produces the same hash.

    For BUY/SELL trades, amount is excluded so overlapping CSV exports with
    slightly different amounts still match.

    For non-trade types (DEP, WDRAW, TRANSFER, etc.), amount IS included
    because two transfers of different amounts on the same date are different
    transactions.

    ``seq`` is a within-file sequence number for rows with identical hash
    inputs: two identical rows in one file get seq 1 and 2 (different
    hashes, both imported), while the same row in two overlapping exports
    both get seq 1 (same hash, second skipped). seq is omitted from the
    hash when it is 1 so hashes of single (non-repeated) rows stay simple
    and stable.
    """
    key_parts = [
        broker or "",
        account_id or "",
        str(date),
        symbol or "",
        tx_type or "",
        f"{Decimal(str(quantity)):.4f}",
        f"{Decimal(str(price)):.4f}",
        currency or "",
    ]

    if settlement_date is not None:
        key_parts.append(str(settlement_date))

    # Include amount for non-trade types
    trade_types = ('BUY', 'SELL')
    if tx_type not in trade_types and amount is not None:
        key_parts.append(f"{Decimal(str(amount)).quantize(Decimal('0.01')):.2f}")

    # seq distinguishes identical rows within one file (omitted when 1)
    if seq != 1:
        key_parts.append(f'seq={seq}')

    key = "|".join(key_parts)
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
        return f"{self.account.name} ({self.currency})"


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
    name = models.CharField(max_length=100)  # account identifier and display name
    # Keyed HMAC-SHA256 of the normalised full account number; the number
    # itself is never stored. Nullable so existing rows migrate cleanly.
    fingerprint = models.CharField(max_length=64, unique=True, null=True, blank=True)
    last_4 = models.CharField(max_length=4, blank=True, default="")
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
    """Tracks historical positions for an account over time.

    Each row represents a position that was active from `date_from` to
    `date_to`. A `NULL` `date_to` means the position is still held.

    Populated from Holdings CSV snapshots. Multiple snapshots for the same
    (account, symbol) create new rows with the previous row's `date_to` set
    to one day before the new snapshot date.
    """

    owner = models.CharField(max_length=50)
    account = models.ForeignKey(
        "Account", on_delete=models.PROTECT, related_name="position_history"
    )
    symbol = models.CharField(max_length=50)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    cost_basis_per_unit = models.DecimalField(max_digits=12, decimal_places=4)
    total_cost_basis = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="CAD")
    market_price = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000"),
        help_text="Price at the time of the snapshot"
    )
    market_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="Market value (quantity * market_price) at snapshot time"
    )
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
    period_start = models.DateField(
        null=True, blank=True,
        help_text="Earliest transaction date in this statement"
    )
    period_end = models.DateField(
        null=True, blank=True,
        help_text="Latest transaction date in this statement"
    )

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
    settlement_date = models.DateField(
        null=True, blank=True,
        help_text="Settlement date (T+2 for most trades)"
    )
    symbol = models.CharField(max_length=50, blank=True, default="")
    type = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    price = models.DecimalField(max_digits=12, decimal_places=4)
    commission = models.DecimalField(max_digits=10, decimal_places=2)
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
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 of the imported PDF; same content again is a duplicate, "
                  "same key with different content is a conflict",
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
        max_length=50,
        help_text='Type of activity from RBC PDF',
        choices=[
            ('BOUGHT', 'Bought (cash purchase)'),
            ('SOLD', 'Sold (sale proceeds)'),
            ('DIST', 'Distribution (cash dividend)'),
            ('DIVIDEND', 'Dividend (cash received)'),
            ('DIV REIN', 'Dividend Reinvestment (net cash = 0)'),
            ('INTEREST_RECEIVED', 'Interest Received (RBF/IB, net cash = 0)'),
            ('INTEREST_PAID', 'Interest Paid (margin debt, cash out)'),
            ('TRANSFER', 'Transfer'),
            ('TFR OUT', 'Transfer Out'),
            ('WIRE TFR', 'Wire Transfer'),
            ('CONTRIB', 'Contribution'),
            ('DEPOSIT', 'Deposit'),
            ('WITHDRAWAL', 'Withdrawal'),
            ('ADJUST', 'Book cost adjustment'),
            ('RTC', 'Book cost adjustment (RTC)'),
            ('FEE', 'Fee/Charge'),
            ('OTHER', 'Other'),
        ],
        default='OTHER',
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
    account_type_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="RRSP, RRIF, RIF, LIF, LIRA, TFSA, RESP",
    )
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 of the imported PDF; same content again is a duplicate, "
                  "same key with different content is a conflict",
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
    statement_date = models.DateField(
        help_text="RBC statement date; calendar month end for gap and replay rows"
    )
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="CAD",
    )
    SOURCE_CHOICES = [("REPLAY", "REPLAY"), ("PDF", "PDF"), ("GAP", "GAP")]
    source = models.CharField(
        max_length=6,
        choices=SOURCE_CHOICES,
        default="REPLAY",
        help_text="How the row was generated: REPLAY (previous PDF statement + CSV "
                  "transactions), PDF (taken from the RBC statement as-is), "
                  "GAP (no PDF statement, no transactions)",
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
    total_deposits = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_withdrawals = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_transfers = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="DIV + DIST + INT for the period (native currency)")
    total_fees = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    total_tax = models.DecimalField(
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
    has_stale_prices = models.BooleanField(
        default=False,
        help_text="True when at least one position is priced from the last known "
                  "PDF price (no PDF holding and no historical close available)",
    )

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


class CoreMonthlyStatementPosition(models.Model):
    """One holding in a CoreMonthlyStatement row's holdings section.

    Quantities are netted per account (across currencies — the DLR journal
    nets to zero and drops out) before being placed in a currency section.
    A detail model instead of JSONField (SQL Server safety, queryable).
    Plan: docs/plans/statements-forward.md.
    """

    PRICE_SOURCE_CHOICES = [
        ("PDF", "PDF"),
        ("HISTORY", "HISTORY"),
        ("LAST_PDF", "LAST_PDF"),
    ]

    statement = models.ForeignKey(
        CoreMonthlyStatement,
        on_delete=models.CASCADE,
        related_name="positions",
    )
    symbol = models.CharField(max_length=50)
    quantity = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000")
    )
    price = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000")
    )
    price_source = models.CharField(
        max_length=8,
        choices=PRICE_SOURCE_CHOICES,
        default="PDF",
        help_text="PDF (holding on this statement), HISTORY (latest close at or "
                  "before the statement date), LAST_PDF (last known PDF price, "
                  "row marked stale)",
    )
    value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        db_table = "core_monthly_statement_position"
        unique_together = [("statement", "symbol")]
        ordering = ["symbol"]

    def __str__(self):
        return f"{self.statement} — {self.symbol}: {self.quantity}"


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


# ============================================================================
# ACCOUNTING LEDGER SYSTEM (Phase F2)
# ============================================================================


class LedgerAccount(models.Model):
    """Represents a single ledger account (cash or position).
    
    Created automatically when first transaction affects that account/symbol/currency.
    Each brokerage account has multiple ledger accounts:
    - One CASH account per currency (e.g., "90000001 CAD Cash")
    - One POSITION account per (symbol, currency) (e.g., "90000001 AAPL USD")
    """
    
    ACCOUNT_TYPES = [
        ('CASH', 'Cash'),
        ('POSITION', 'Stock Position'),
        ('INCOME', 'Income'),
    ]
    
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="ledger_accounts",
        help_text="Parent brokerage account"
    )
    # Denormalized fields for quick lookup without joins
    account_name = models.CharField(
        max_length=100,
        help_text="Denormalized account name (e.g., 'ACCOUNT_A')"
    )
    ledger_name = models.CharField(
        max_length=150, db_index=True,
        help_text="Human-readable name: name-type-currency[-symbol]"
    )
    account_type = models.CharField(max_length=10, choices=ACCOUNT_TYPES)
    currency = models.CharField(max_length=3)
    symbol = models.CharField(
        max_length=50, 
        blank=True, 
        default="",
        help_text="Only for POSITION type"
    )
    
    # Current balance (updated as entries are added)
    quantity = models.DecimalField(
        max_digits=15, decimal_places=4, default=Decimal("0.0000"),
        help_text="Shares (POSITION) or 0 (CASH)"
    )
    cost_basis_total = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="Total cost basis"
    )
    cost_basis_per_unit = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0.0000"),
        help_text="Average cost per share"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "core_ledger_account"
        unique_together = [('account', 'account_type', 'currency', 'symbol')]
        indexes = [
            models.Index(fields=["account", "account_type"]),
            models.Index(fields=["account", "symbol", "currency"]),
        ]
    
    def __str__(self):
        return self.ledger_name


class LedgerEntry(models.Model):
    """Individual ledger transaction (double-entry).
    
    Every Transaction creates 1-2 LedgerEntry records:
    - BUY: CREDIT cash + DEBIT position
    - SELL: DEBIT cash + CREDIT position
    - DIV/DIST/INT: DEBIT cash only
    - DEP/CONTRIB: DEBIT cash only
    - WDRAW/TFR OUT: CREDIT cash only
    - DIV REIN: DEBIT position + CREDIT cash (dividend used)
    - SPLIT: DEBIT position (quantity up, cost basis per share down)
    
    Audit trail: Every entry links back to source Transaction.
    """
    
    ENTRY_TYPES = [
        ('DEBIT', 'Debit'),
        ('CREDIT', 'Credit'),
    ]
    
    ledger_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.CASCADE,
        related_name="entries"
    )
    entry_date = models.DateField()
    entry_type = models.CharField(max_length=6, choices=ENTRY_TYPES)
    
    # For CASH accounts
    cash_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="Positive for DEBIT, negative for CREDIT"
    )
    
    # For POSITION accounts
    quantity_change = models.DecimalField(
        max_digits=15, decimal_places=4, default=Decimal("0.0000"),
        help_text="Shares bought/sold"
    )
    cost_basis_change = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="Cost basis change"
    )
    
    # Related symbol (for cash entries, shows which security the transaction was for)
    related_symbol = models.CharField(
        max_length=50, blank=True, default="",
        help_text="For cash entries: which security this transaction was for"
    )
    
    # Audit trail: link back to source Transaction
    source_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
        help_text="Source transaction (for audit trail)"
    )
    description = models.CharField(max_length=255, blank=True, default="")
    
    # Period tracking
    statement_month = models.DateField(
        help_text="First day of month (for period reporting)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = "core_ledger_entry"
        ordering = ["-entry_date", "-created_at"]
        indexes = [
            models.Index(fields=["ledger_account", "statement_month"]),
            models.Index(fields=["source_transaction"]),
            models.Index(fields=["entry_date"]),
        ]
    
    def __str__(self):
        return f"{self.entry_type} {self.ledger_account} {self.entry_date}"


class MonthlyLedgerBalance(models.Model):
    """Month-end snapshot of all ledger accounts.
    
    Built from LedgerEntry records for the month.
    Verified against Holdings CSV and RBC PDF.
    """
    
    ledger_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.CASCADE,
        related_name="monthly_balances"
    )
    statement_month = models.DateField(help_text="First day of month")
    
    # Balances at month end (computed from LedgerEntry)
    quantity = models.DecimalField(
        max_digits=15, decimal_places=4, default=Decimal("0.0000")
    )
    cost_basis_total = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    cost_basis_per_unit = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0.0000")
    )
    
    # Market values (from Holdings CSV or RBC PDF for verification)
    market_price_csv = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="From Holdings CSV"
    )
    market_value_csv = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    market_price_rbc = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="From RBC PDF"
    )
    market_value_rbc = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    
    # Reconciliation status
    quantity_match_csv = models.BooleanField(
        null=True, blank=True,
        help_text="Matches Holdings CSV?"
    )
    quantity_match_rbc = models.BooleanField(
        null=True, blank=True,
        help_text="Matches RBC PDF?"
    )
    reconciliation_notes = models.TextField(blank=True, default="")
    
    # Month closure
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.CharField(max_length=100, blank=True, default="")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "core_monthly_ledger_balance"
        unique_together = [('ledger_account', 'statement_month')]
        ordering = ["-statement_month"]
        indexes = [
            models.Index(fields=["ledger_account", "statement_month"]),
            models.Index(fields=["statement_month", "is_closed"]),
        ]
    
    def __str__(self):
        return f"{self.ledger_account} {self.statement_month}"


class LedgerMonthClosure(models.Model):
    """Tracks month-end closure status for each account.
    
    Once closed, no LedgerEntry can be added/modified for that month.
    """
    
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="ledger_closures"
    )
    statement_month = models.DateField(help_text="First day of month")
    currency = models.CharField(max_length=3)
    
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.CharField(max_length=100, blank=True, default="")
    closure_notes = models.TextField(blank=True, default="")
    
    # Verification results
    csv_holdings_verified = models.BooleanField(
        null=True, blank=True,
        help_text="Verified against Holdings CSV?"
    )
    rbc_pdf_verified = models.BooleanField(
        null=True, blank=True,
        help_text="Verified against RBC PDF?"
    )
    verification_report_path = models.CharField(
        max_length=500, blank=True, default=""
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "core_ledger_month_closure"
        unique_together = [('account', 'statement_month', 'currency')]
        ordering = ["-statement_month"]
        indexes = [
            models.Index(fields=["account", "statement_month", "currency"]),
            models.Index(fields=["is_closed"]),
        ]
    
    def __str__(self):
        status = "CLOSED" if self.is_closed else "OPEN"
        return f"{self.account.name} {self.currency} {self.statement_month} [{status}]"


class MonthlyCheckResult(models.Model):
    """Stored verdict of one (account, statement_date, currency) monthly check.

    Written by ``monthly_check --store`` (phase 4, step 14b). One row per
    unique key; re-runs upsert in place. Account is shown by name only —
    never digits.
    """

    VERDICT_PASS = "PASS"
    VERDICT_FAIL = "FAIL"
    VERDICT_NO_CSV = "NO_CSV"
    VERDICT_FIRST = "FIRST"
    VERDICT_CHOICES = [
        (VERDICT_PASS, "PASS"),
        (VERDICT_FAIL, "FAIL"),
        (VERDICT_NO_CSV, "NO_CSV"),
        (VERDICT_FIRST, "FIRST"),
    ]

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="monthly_check_results"
    )
    statement_date = models.DateField()
    currency = models.CharField(max_length=3)
    verdict = models.CharField(max_length=8, choices=VERDICT_CHOICES)
    notes = models.TextField(blank=True, default="")
    # Set only on create (a re-run updates the verdict, not checked_at).
    checked_at = models.DateTimeField()

    class Meta:
        db_table = "core_monthly_check_result"
        unique_together = [("account", "statement_date", "currency")]
        ordering = ["account", "statement_date", "currency"]
        indexes = [
            models.Index(fields=["account", "statement_date"]),
        ]

    def __str__(self):
        return f"{self.account.name} {self.statement_date} [{self.currency}] {self.verdict}"


class MonthlyException(models.Model):
    """One exception found by ``monthly_check`` (phase 4, step 14b).

    Keyed by kind+account+statement_date+currency+ref; ``--store`` upserts,
    resolves rows that vanished and reopens accepted rows whose difference
    changed. Accepting is a note only — it never changes imported data.
    ``currency`` and ``ref`` are blank (never NULL) so the unique constraint
    works on SQLite/SQL Server; QUANTITY uses currency="" (per account),
    IMPORT uses ref=<hash8>.
    """

    KIND_CASH = "CASH"
    KIND_QUANTITY = "QUANTITY"
    KIND_CHAIN = "CHAIN"
    KIND_IMPORT = "IMPORT"
    KIND_CHOICES = [
        (KIND_CASH, "CASH"),
        (KIND_QUANTITY, "QUANTITY"),
        (KIND_CHAIN, "CHAIN"),
        (KIND_IMPORT, "IMPORT"),
    ]

    STATUS_OPEN = "open"
    STATUS_ACCEPTED = "accepted"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_OPEN, "open"),
        (STATUS_ACCEPTED, "accepted"),
        (STATUS_RESOLVED, "resolved"),
    ]

    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="monthly_exceptions"
    )
    statement_date = models.DateField()
    currency = models.CharField(max_length=3, blank=True, default="")
    # Symbol for QUANTITY, hash8 for IMPORT, blank otherwise.
    ref = models.CharField(max_length=200, blank=True, default="")
    difference = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    detail = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=8, choices=STATUS_CHOICES, default=STATUS_OPEN
    )
    reason = models.TextField(blank=True, default="")
    accepted_at = models.DateTimeField(null=True, blank=True)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_monthly_exception"
        unique_together = [
            ("kind", "account", "statement_date", "currency", "ref")
        ]
        ordering = ["account", "statement_date", "kind", "currency", "ref"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["account", "statement_date"]),
        ]

    def __str__(self):
        return (
            f"{self.account.name} {self.statement_date} "
            f"{self.kind} [{self.currency or '-'}] {self.ref or '-'} "
            f"[{self.status}]"
        )
