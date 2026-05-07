from django.contrib import admin
from .models import BrokerStatement, Transaction, Account, TransactionType, FileImportLog


@admin.register(BrokerStatement)
class BrokerStatementAdmin(admin.ModelAdmin):
    list_display = ['source', 'file_date', 'raw_filename']
    list_filter = ['source']
    search_fields = ['source', 'raw_filename']


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ['account_id', 'name', 'broker', 'currency', 'starting_balance']
    search_fields = ['account_id', 'name']
    list_filter = ['currency', 'broker']


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
