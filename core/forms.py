from django import forms
from django.forms import inlineformset_factory

from core.models import Account, AccountCurrency


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = [
            'name', 'broker', 'category', 'owner',
        ]
        # Removed deprecated: currency, starting_balance, starting_balance_date


class AccountCurrencyInlineFormSet(inlineformset_factory(
    Account, AccountCurrency,
    fields=['currency', 'starting_balance', 'starting_balance_date'],
    extra=0,
    can_delete=True,
)):
    """Formset for editing AccountCurrency sub-accounts on the account edit form."""

    starting_balance = forms.DecimalField(
        max_digits=15, decimal_places=2, required=False, initial=0
    )


AccountCurrencyFormSet = inlineformset_factory(
    Account, AccountCurrency,
    fields=['currency', 'starting_balance', 'starting_balance_date'],
    extra=0,
    can_delete=True,
)
