# Spec: Account Management UI
Project name: portfolio_manager
Customer: tdwdev
**Branch:** `feature/account-management-ui`
**Base branch:** `dev`
**Project path:** `C:\AI-DevTeam\projects\portfolio_manager`
**Test command:** `python manage.py test core --settings=portfolio_manager.settings_test`

---

## Goal
Allow users to create, edit, and delete accounts in the browser.
Currently accounts only appear after a CSV import. There is no way to set them up manually.

---

## Existing Code to Read First

Before writing any code, read these files to understand the current state:

- `core/models.py` — `Account` model (fields: account_id, name, broker, currency, starting_balance, starting_balance_date)
- `core/views.py` — existing function-based views (use same style: render, get_object_or_404, redirect)
- `core/urls.py` — existing URL patterns (4 paths currently)
- `core/templates/core/accounts.html` — accounts list template (table with 8 columns + account_id link)
- `core/templates/core/base.html` — base template to extend

---

## Files to Create

### 1. `core/forms.py`
Create a `ModelForm` for `Account`:

```python
from django import forms
from core.models import Account

class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ['account_id', 'name', 'broker', 'currency', 'starting_balance', 'starting_balance_date']
        widgets = {
            'starting_balance_date': forms.DateInput(attrs={'type': 'date'}),
        }
```

**Important:** When used in the edit view, the `account_id` field must be set to `disabled=True` on the form instance so users cannot change it (account_id is the primary business key).

---

### 2. `core/templates/core/account_form.html`
A template extending `core/base.html` for both create and edit:
- `<h1>` should say "Add Account" or "Edit Account" — use a `{{ title }}` context variable
- Render the form with `{{ form.as_p }}`
- Submit button: "Save Account"
- Cancel link: `<a href="{% url 'accounts_list' %}">Cancel</a>`
- Include `{% csrf_token %}` inside the form

---

## Files to Modify

### 3. `core/views.py`
Add three new function-based views. Import `redirect` from `django.shortcuts` and `AccountForm` from `core.forms`.

#### `account_create(request)`
- GET: render `core/account_form.html` with a blank `AccountForm` and `title='Add Account'`
- POST: validate form; if valid, save and `redirect('accounts_list')`; if invalid, re-render with errors

#### `account_edit(request, account_id)`
- Fetch account with `get_object_or_404(Account, account_id=account_id)`
- GET: render form pre-populated with `instance=account`; set `form.fields['account_id'].disabled = True`
- POST: validate `AccountForm(request.POST, instance=account)`; also set `account_id` field disabled; if valid, save and `redirect('account_detail', account_id=account.account_id)`; if invalid, re-render with errors and title `'Edit Account'`

#### `account_delete(request, account_id)`
- Only accept POST; return `HttpResponseNotAllowed(['POST'])` for any other method
- Fetch account with `get_object_or_404(Account, account_id=account_id)`
- Call `account.delete()` and `redirect('accounts_list')`
- Do NOT show a confirmation page — the confirmation is handled by JavaScript in the template

---

### 4. `core/urls.py`
Add three new paths. **Order matters:** `accounts/new/` must appear **before** `accounts/<str:account_id>/` so "new" is not interpreted as an account_id.

```python
path('accounts/new/', views.account_create, name='account_create'),
path('accounts/<str:account_id>/edit/', views.account_edit, name='account_edit'),
path('accounts/<str:account_id>/delete/', views.account_delete, name='account_delete'),
```

The existing `accounts/<str:account_id>/` detail path stays in place.

---

### 5. `core/templates/core/accounts.html`
Two changes:

**A.** Add an "Add Account" button above the table:
```html
<a href="{% url 'account_create' %}">Add Account</a>
```

**B.** Add an "Actions" column to the table. In `<thead>` add `<th>Actions</th>`. In `<tbody>` rows add:
```html
<td>
    <a href="{% url 'account_edit' account.account_id %}">Edit</a>
    <form method="post" action="{% url 'account_delete' account.account_id %}" style="display:inline">
        {% csrf_token %}
        <button type="submit" onclick="return confirm('Delete account {{ account.account_id }}?')">Delete</button>
    </form>
</td>
```

---

## Files to Create

### 6. `core/tests/test_account_crud.py`
Write a `TestCase` class `AccountCRUDTests` with these tests:

- `test_account_list_loads` — GET `/accounts/` returns 200
- `test_account_create_get` — GET `/accounts/new/` returns 200 and contains "Add Account"
- `test_account_create_post_valid` — POST to `/accounts/new/` with valid data creates an Account and redirects (302) to accounts list
- `test_account_create_post_invalid` — POST with missing `name` returns 200 with form errors (does not create account)
- `test_account_edit_get` — GET `/accounts/<account_id>/edit/` returns 200 and contains the account name
- `test_account_edit_post_valid` — POST with updated name redirects and name is changed in DB
- `test_account_delete_post` — POST to `/accounts/<account_id>/delete/` deletes the account and redirects to accounts list
- `test_account_delete_get_not_allowed` — GET to delete URL returns 405

Use `setUp` to create a test account:
```python
self.account = Account.objects.create(
    account_id='TEST001',
    name='Test Account',
    broker='IB',
    currency='USD',
    starting_balance=Decimal('1000.00'),
)
```

---

## Constraints

- Do NOT change `core/models.py`
- Do NOT create any migrations
- Do NOT add authentication or permission checks
- On delete: transactions linked to this account have `on_delete=SET_NULL` — this is correct behaviour, do not block deletion
- Follow function-based view style already used in `core/views.py`
- All templates must extend `core/base.html`
- Run `python manage.py test core --settings=portfolio_manager.settings_test` to verify. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: account management UI (create, edit, delete)"`
