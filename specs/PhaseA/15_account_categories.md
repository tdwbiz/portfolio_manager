# Feature Spec: Account Categories and Owners

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/account-categories

---

## Context

Specs 01–14 are complete. The `Account` model (core/models.py) currently has these fields:
`account_id`, `name`, `broker`, `currency`, `starting_balance`, `starting_balance_date`.

There is no way to classify accounts by tax treatment (RRSP, TFSA, Taxable, etc.) or by
the person who owns them. All accounts are treated identically in every report. Adding
`category` and `owner` fields to `Account` is a prerequisite for Spec 16 (Portfolio
Summary Enhancements) which groups and summarises by category.

This spec delivers:
1. Two new optional fields on `Account`: `category` and `owner`
2. Updated admin, form, and account list/detail templates to show the new fields
3. A migration for the new fields

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. Update `core/models.py` — Add fields to `Account`

Add the following two fields to the `Account` model, after the existing `currency` field:

```python
CATEGORY_CHOICES = [
    ('RRSP',    'RRSP (Retirement)'),
    ('LIRA',    'LIRA (Locked-In Retirement)'),
    ('TFSA',    'TFSA (Tax-Free)'),
    ('TAXABLE', 'Taxable'),
    ('RESP',    'RESP (Education)'),
    ('OTHER',   'Other'),
]

category = models.CharField(
    max_length=10,
    choices=CATEGORY_CHOICES,
    default='TAXABLE',
    help_text='Account tax category (RRSP, TFSA, Taxable, etc.)'
)
owner = models.CharField(
    max_length=50,
    blank=True,
    default='',
    help_text='Person or entity this account belongs to (e.g. "Tom", "Wife", "Joint")'
)
```

- `category` is required with a default of `'TAXABLE'` so all existing accounts receive a
  sensible value after migration.
- `owner` is optional (blank=True) to keep backward compatibility.
- Do not remove or change any existing fields.

### 2. Migration

Create a migration for the two new fields via `makemigrations`.
The migration file will be `core/migrations/0009_account_category_owner.py` (exact number
assigned by Django — do not hardcode).

> **This is a manual step.** The agent must not run `migrate`. See Manual Steps.

### 3. Update `core/admin.py` — `AccountAdmin`

Update the `list_display` and add `list_filter` for the new fields:

```python
@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ['account_id', 'name', 'broker', 'category', 'owner', 'currency', 'starting_balance']
    search_fields = ['account_id', 'name', 'owner']
    list_filter = ['broker', 'category', 'owner']
```

### 4. Update `core/forms.py` — `AccountForm`

Add `category` and `owner` to the fields list:

```python
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
```

### 5. Update `core/templates/core/accounts.html` — Accounts list table

Add two new columns after `Currency`:

- Add `<th>Category</th>` and `<th>Owner</th>` in the header row.
- Add `<td>{{ account.get_category_display }}</td>` and `<td>{{ account.owner|default:"—" }}</td>`
  in the matching body row.

Do not remove any existing columns.

### 6. Update `core/templates/core/account_detail.html` — Account header

In the `<p>` block that shows account metadata at the top of the detail page, add
category and owner after the existing broker line:

```html
<strong>Category:</strong> {{ account.get_category_display }} &nbsp;|&nbsp;
<strong>Owner:</strong> {{ account.owner|default:"—" }}
```

### 7. Update `core/templates/core/dashboard.html` — Accounts overview table

Add `Category` and `Owner` columns to the accounts overview table on the dashboard,
matching the same position as in the accounts list (after `Currency`).

### 8. Tests — `core/tests/test_account_crud.py`

The existing test file tests account CRUD. Add the following test methods to the
existing test class (do not replace existing tests):

- `test_account_default_category`: create an Account without specifying `category`;
  assert `account.category == 'TAXABLE'`.
- `test_account_category_choices`: create accounts with each of the six category values
  and assert `get_category_display()` returns the expected human-readable label.
- `test_account_owner_optional`: create an Account without `owner`; assert
  `account.owner == ''` and it saves without error.
- `test_account_form_includes_category_owner`: instantiate `AccountForm` and assert that
  `'category'` and `'owner'` are in `form.fields`.
- `test_accounts_list_shows_category`: create an account with `category='RRSP'`, log in,
  GET `/accounts/`, assert response contains `'RRSP (Retirement)'`.

Use `self.client.force_login(user)` for view tests. Create a `User` in `setUp` if not
already present.

Run with: `python manage.py test core --settings=portfolio_manager.settings_test`

---

## Constraints

- Do NOT run `migrate` — that is a manual step.
- Do NOT remove or rename any existing `Account` fields.
- Do NOT change any existing migrations.
- The `CATEGORY_CHOICES` constant must be defined on the `Account` model class, not at
  module level, so it is accessible as `Account.CATEGORY_CHOICES` in tests.
- Follow PEP 8. Add type hints to any new functions.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

After the agent completes all code changes:

- [ ] `python manage.py makemigrations --settings=portfolio_manager.settings_test`
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_test`
- [ ] `python manage.py makemigrations` (against real DB settings)
- [ ] `python manage.py migrate` (against real DB — PortfolioDEV)
- [ ] Review migration file before running against production DB
- [ ] `git add -A && git commit -m "feat: add category and owner fields to Account"`
- [ ] `git push origin feature/account-categories`

---

## Human Approval Required

Human approval required before any migration is run against the production database
(PortfolioDEV) and before any git push.
