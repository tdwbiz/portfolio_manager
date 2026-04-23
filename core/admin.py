from django.contrib import admin
from .models import BrokerStatement, Transaction

admin.site.register(BrokerStatement)
admin.site.register(Transaction)
