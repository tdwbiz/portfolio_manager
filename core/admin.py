from django.contrib import admin
from .models import BrokerStatement, Transaction, Account

admin.site.register(BrokerStatement)
admin.site.register(Transaction)
admin.site.register(Account, list_display=['account_id', 'name', 'broker', 'currency', 'starting_balance'])
