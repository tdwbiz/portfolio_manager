from django.contrib import admin
from .models import BrokerStatement, Transaction, Account, TransactionType, FileImportLog, BrandingConfig
from .models import RbcMonthly, RbcMonthlyBalance, RbcMonthlyTransaction, RbcAnnual


@admin.register(BrandingConfig)
class BrandingConfigAdmin(admin.ModelAdmin):
    fieldsets = [
        ('Company Information', {
            'fields': ['company_name', 'company_logo_url', 'tagline', 'show_logo', 'show_tagline']
        }),
        ('Color Scheme', {
            'fields': ['primary_color', 'secondary_color', 'success_color', 'danger_color',
                      'navbar_bg_color', 'navbar_text_color', 'footer_bg_color']
        }),
        ('Footer', {
            'fields': ['footer_text']
        }),
    ]

    def has_add_permission(self, request):
        # Only allow one instance
        return not BrandingConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Don't allow deletion of the singleton
        return False


@admin.register(BrokerStatement)
class BrokerStatementAdmin(admin.ModelAdmin):
    list_display = ['source', 'file_date', 'raw_filename']
    list_filter = ['source']
    search_fields = ['source', 'raw_filename']


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ['account_id', 'name', 'broker', 'category', 'owner', 'currency', 'starting_balance']
    search_fields = ['account_id', 'name', 'owner']
    list_filter = ['broker', 'category', 'owner']


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ['date', 'symbol', 'type', 'quantity', 'amount_cad', 'account', 'statement']
    list_filter = ['type', 'statement__source', 'account']
    search_fields = ['symbol', 'notes']
    date_hierarchy = 'date'


@admin.register(TransactionType)
class TransactionTypeAdmin(admin.ModelAdmin):
    list_display = ['code', 'label', 'affects_position', 'affects_cash']
    ordering = ['code']


@admin.register(FileImportLog)
class FileImportLogAdmin(admin.ModelAdmin):
    list_display = ['filename', 'broker', 'imported_at', 'status', 'records_imported', 'records_skipped']
    list_filter = ['status', 'broker']
    search_fields = ['filename']
    readonly_fields = ['imported_at', 'records_imported', 'records_skipped', 'error_message']


@admin.register(RbcMonthly)
class RbcMonthlyAdmin(admin.ModelAdmin):
    list_display = ['account', 'statement_date', 'account_number', 'begin_total_market_value', 'end_total_market_value', 'file_name']
    list_filter = ['account']
    search_fields = ['account_number', 'file_name']
    date_hierarchy = 'statement_date'


@admin.register(RbcAnnual)
class RbcAnnualAdmin(admin.ModelAdmin):
    list_display = ['account', 'report_year', 'currency', 'bal_ending_value', 'bal_net_return', 'chg_total_paid']
    list_filter = ['account', 'currency', 'report_year']
    search_fields = ['account_number', 'file_name']
    readonly_fields = ['parsed_at']
