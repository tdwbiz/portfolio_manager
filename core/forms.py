from django import forms
from django.forms import inlineformset_factory

from core.models import Account, AccountCurrency


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = [
            'account_id', 'name', 'broker', 'category', 'owner',
        ]
        # Removed deprecated: currency, starting_balance, starting_balance_date

    def clean_account_id(self):
        """Reject number-valued account ids (decision 3, plan account-names).

        The real RBC number is only ever set via `set_account_fingerprint`;
        this field is a label and must not contain digits.
        """
        value = self.cleaned_data.get('account_id') or ''
        if any(ch.isdigit() for ch in value):
            raise forms.ValidationError(
                'Account ID must not contain digits; use a name. '
                'The account number is set via the set_account_fingerprint command.'
            )
        return value


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
