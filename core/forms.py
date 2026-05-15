from django import forms
from core.models import Account


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = [
            'account_id', 'name', 'broker', 'category', 'owner',
            'currency', 'starting_balance', 'starting_balance_date'
        ]
        widgets = {
            'starting_balance_date': forms.DateInput(attrs={'type': 'date'}),
        }
