from django.contrib import admin

from .models import (
    Account,
    AccountCurrency,
    BrandingConfig,
    BrokerStatement,
    CoreAnnualReport,
    CoreMonthlyStatement,
    CoreReconciliation,
    FileImportLog,
    FixedPriceConfig,
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
    list_display = ["source", "file_date", "raw_filename"]
    list_filter = ["source"]
    search_fields = ["source", "raw_filename"]


class AccountCurrencyInline(admin.TabularInline):
    model = AccountCurrency
    extra = 0
    fields = ["currency", "starting_balance", "starting_balance_date"]
    readonly_fields = []


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = [
        "account_id",
        "name",
        "broker",
        "category",
        "owner",
        "currency",
    ]
    search_fields = ["account_id", "name", "owner"]
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
        "account_number",
        "begin_total_market_value",
        "end_total_market_value",
        "file_name",
    ]
    list_filter = ["account"]
    search_fields = ["account_number", "file_name"]
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
    search_fields = ["account_number", "file_name"]
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
    search_fields = ["account__account_id"]
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
    search_fields = ["account__account_id"]
    readonly_fields = ["generated_at"]


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
    search_fields = ["account__account_id"]
    date_hierarchy = "period_start"
    readonly_fields = ["reconciled_at", "reconciled_by"]
