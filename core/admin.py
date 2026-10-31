from django.contrib import admin

from .models import (
    Account,
    AccountCurrency,
    AccountOwner,
    BrandingConfig,
    BrokerStatement,
    CoreAnnualReport,
    CoreMonthlyStatement,
    CoreReconciliation,
    FileImportLog,
    FixedPriceConfig,
    MonthlyCheckResult,
    MonthlyException,
    OpeningPosition,
    Owner,
    RbcAnnual,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
    Transaction,
    TransactionType,
)


@admin.register(BrandingConfig)
class BrandingConfigAdmin(admin.ModelAdmin):
    fieldsets = [
        (
            "Company Information",
            {
                "fields": [
                    "company_name",
                    "company_logo_url",
                    "tagline",
                    "show_logo",
                    "show_tagline",
                ]
            },
        ),
        (
            "Color Scheme",
            {
                "fields": [
                    "primary_color",
                    "secondary_color",
                    "success_color",
                    "danger_color",
                    "navbar_bg_color",
                    "navbar_text_color",
                    "footer_bg_color",
                ]
            },
        ),
        ("Footer", {"fields": ["footer_text"]}),
    ]

    def has_add_permission(self, request):
        # Only allow one instance
        return not BrandingConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Don't allow deletion of the singleton
        return False


@admin.register(BrokerStatement)
class BrokerStatementAdmin(admin.ModelAdmin):
    # The original file name is deliberately not shown or searched: it carries
    # the real account number (docs/architecture.md — name and last 4 only).
    list_display = ["source", "file_date"]
    list_filter = ["source"]
    search_fields = ["source"]


class AccountCurrencyInline(admin.TabularInline):
    model = AccountCurrency
    extra = 0
    fields = ["currency", "starting_balance", "starting_balance_date"]
    readonly_fields = []


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "last_4",
        "broker",
        "category",
        "owner",
        "currency",
    ]
    search_fields = ["name", "last_4", "owner"]
    list_filter = ["broker", "category", "owner"]
    inlines = [AccountCurrencyInline]


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = [
        "date",
        "symbol",
        "type",
        "quantity",
        "amount_cad",
        "account",
        "statement",
    ]
    list_filter = ["type", "statement__source", "account"]
    search_fields = ["symbol", "notes"]
    date_hierarchy = "date"


@admin.register(TransactionType)
class TransactionTypeAdmin(admin.ModelAdmin):
    list_display = ["code", "label", "affects_position", "affects_cash"]
    ordering = ["code"]


@admin.register(FileImportLog)
class FileImportLogAdmin(admin.ModelAdmin):
    list_display = [
        "filename",
        "broker",
        "imported_at",
        "status",
        "records_imported",
        "records_skipped",
    ]
    list_filter = ["status", "broker"]
    search_fields = ["filename"]
    readonly_fields = [
        "imported_at",
        "records_imported",
        "records_skipped",
        "error_message",
    ]


@admin.register(RbcMonthly)
class RbcMonthlyAdmin(admin.ModelAdmin):
    list_display = [
        "account",
        "statement_date",
        "begin_total_market_value",
        "end_total_market_value",
        "file_name",
    ]
    list_filter = ["account"]
    search_fields = ["account__name", "file_name"]
    date_hierarchy = "statement_date"


@admin.register(RbcMonthlyHolding)
class RbcMonthlyHoldingAdmin(admin.ModelAdmin):
    list_display = [
        "rbc_monthly",
        "currency",
        "security",
        "symbol",
        "quantity",
        "book_cost",
        "market_price",
        "market_value",
    ]
    list_filter = ["currency", "rbc_monthly"]
    search_fields = ["security", "symbol"]


@admin.register(RbcAnnual)
class RbcAnnualAdmin(admin.ModelAdmin):
    list_display = [
        "account",
        "report_year",
        "currency",
        "bal_ending_value",
        "bal_net_return",
        "chg_total_paid",
    ]
    list_filter = ["account", "currency", "report_year"]
    search_fields = ["account__name", "file_name"]
    readonly_fields = ["parsed_at"]


@admin.register(FixedPriceConfig)
class FixedPriceConfigAdmin(admin.ModelAdmin):
    list_display = ["symbol", "currency", "price_cad", "price_usd", "is_active"]
    list_filter = ["currency", "is_active"]
    search_fields = ["symbol", "notes"]


@admin.register(CoreMonthlyStatement)
class CoreMonthlyStatementAdmin(admin.ModelAdmin):
    list_display = [
        "account",
        "statement_month",
        "currency",
        "ending_market_value",
        "total_transactions",
    ]
    list_filter = ["currency", "account"]
    search_fields = ["account__name"]
    date_hierarchy = "statement_month"
    readonly_fields = ["generated_at"]


@admin.register(CoreAnnualReport)
class CoreAnnualReportAdmin(admin.ModelAdmin):
    list_display = [
        "account",
        "report_year",
        "currency",
        "ending_value",
        "total_transactions",
    ]
    list_filter = ["currency", "account", "report_year"]
    search_fields = ["account__name"]
    readonly_fields = ["generated_at"]


class ReadOnlyMixin:
    """Read-only admin: no add/change/delete, every field readonly."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MonthlyCheckResult)
class MonthlyCheckResultAdmin(ReadOnlyMixin, admin.ModelAdmin):
    # Read-only list; the account is shown by name only — never digits.
    list_display = [
        "account",
        "statement_date",
        "currency",
        "verdict",
        "checked_at",
    ]
    list_filter = ["verdict", "currency", "account"]
    search_fields = ["account__name"]
    date_hierarchy = "statement_date"
    readonly_fields = [
        "account",
        "statement_date",
        "currency",
        "verdict",
        "notes",
        "checked_at",
    ]


@admin.register(MonthlyException)
class MonthlyExceptionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    # Read-only list; the account is shown by name only — never digits.
    list_display = [
        "account",
        "statement_date",
        "kind",
        "currency",
        "ref",
        "difference",
        "status",
        "reason",
        "first_seen",
        "last_seen",
        "resolved_at",
    ]
    list_filter = ["kind", "status", "account"]
    search_fields = ["account__name", "ref"]
    date_hierarchy = "statement_date"
    readonly_fields = [
        "kind",
        "account",
        "statement_date",
        "currency",
        "ref",
        "difference",
        "detail",
        "status",
        "reason",
        "accepted_at",
        "first_seen",
        "last_seen",
        "resolved_at",
    ]


@admin.register(CoreReconciliation)
class CoreReconciliationAdmin(admin.ModelAdmin):
    list_display = [
        "account",
        "reconciliation_type",
        "period_start",
        "period_end",
        "matching",
        "discrepancy_amount",
    ]
    list_filter = ["reconciliation_type", "matching", "account"]
    search_fields = ["account__name"]
    date_hierarchy = "period_start"
    readonly_fields = ["reconciled_at", "reconciled_by"]


@admin.register(Owner)
class OwnerAdmin(ReadOnlyMixin, admin.ModelAdmin):
    # Read-only list; owners are first names only.
    list_display = ["name"]
    search_fields = ["name"]
    readonly_fields = ["name"]


@admin.register(AccountOwner)
class AccountOwnerAdmin(ReadOnlyMixin, admin.ModelAdmin):
    # Read-only list; the account is shown by name only — never digits.
    list_display = ["account", "owner", "share_percent"]
    list_filter = ["account"]
    search_fields = ["account__name", "owner__name"]
    readonly_fields = ["account", "owner", "share_percent"]


@admin.register(OpeningPosition)
class OpeningPositionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    # Read-only list; the account is shown by name only — never digits.
    list_display = [
        "account",
        "symbol",
        "security_name",
        "currency",
        "quantity",
        "book_cost",
        "source",
        "statement",
        "updated_at",
    ]
    list_filter = ["source", "currency", "account"]
    search_fields = ["account__name", "symbol", "security_name"]
    readonly_fields = [
        "account",
        "statement",
        "symbol",
        "security_name",
        "currency",
        "quantity",
        "book_cost",
        "source",
        "note",
        "created_at",
        "updated_at",
    ]
