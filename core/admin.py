from django.contrib import admin
from .models import BrokerStatement, Transaction, Account, TransactionType

admin.site.register(BrokerStatement)
admin.site.register(Transaction)
admin.site.register(Account, list_display=['account_id', 'name', 'broker', 'currency', 'starting_balance'])

@admin.register(TransactionType)
class TransactionTypeAdmin(admin.ModelAdmin):
    list_display  = ['code', 'label', 'affects_position', 'affects_cash']
    ordering      = ['code']
