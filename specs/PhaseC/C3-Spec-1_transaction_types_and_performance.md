# Feature Spec: Transaction Type Registry & Portfolio View Optimization

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature_rbc_pdf_fixes

---

## Context

Two performance and quality issues:

1. **Transaction type normalization lacks canonical mapping** (`imports/parsers.py`): While `_IB_TYPE_MAP` and `_RBC_TYPE_MAP` normalize types to canonical codes (BUY, SELL, DIV, etc.), there's no mechanism to handle future broker-specific codes. The fallback `re.sub(r'[^A-Z0-9]', '_', raw.upper())[:20]` creates inconsistent codes like `FOREIGN_TAX_WITHOLDING` vs `WITHTAX`.

2. **Portfolio view loads all transactions into memory** (`core/views_accounts.py`): The `account_detail` view loads all transactions for an account into Python memory to compute holdings. With 10,000+ transactions, this becomes slow and memory-intensive.

**Related issues:** M1 (type normalization), M2 (memory-heavy portfolio view).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. **Add centralized transaction type registry**

#### 1a. Add `auto_create_or_get()` to `TransactionType` model

- Add a class method to `core/models.py` `TransactionType` model
- Automatically creates or retrieves a `TransactionType` record for any code
- Ensures consistent labeling across the system

```python
@classmethod
def auto_create_or_get(cls, code, label):
    """Get or create a TransactionType record.
    
    Args:
        code: Canonical code (e.g., 'BUY', 'SELL', 'DIV')
        label: Human-readable label (e.g., 'Buy', 'Sell', 'Dividend')
    
    Returns:
        TransactionType instance
    """
    obj, created = cls.objects.get_or_create(
        code=code,
        defaults={'label': label}
    )
    return obj
```

#### 1b. Update type normalization to use the registry

- In `imports/parsers.py`, update `_normalize_type()` to call `TransactionType.auto_create_or_get()`
- Ensure the canonical code is always used consistently
- Log when a new type is auto-created

```python
from core.models import TransactionType

def _normalize_type(raw: str, type_map: Dict[str, Tuple[str, str]]) -> Tuple[str, str]:
    """Return (code, label) for a raw broker type string.
    
    Falls back to a sanitised uppercase code for any unknown type so that
    future broker inventions are stored without requiring a code change.
    """
    if raw in type_map:
        code, label = type_map[raw]
        TransactionType.auto_create_or_get(code, label)
        return code, label
    
    code = re.sub(r'[^A-Z0-9]', '_', raw.upper())[:20].strip('_') or 'OTHER'
    label = raw
    TransactionType.auto_create_or_get(code, label)
    return code, label
```

### 2. **Optimize portfolio view with database aggregation**

#### 2a. Replace in-memory transaction loading with database aggregation

- In `core/views_accounts.py`, replace the loop that loads all transactions into Python memory
- Use Django's `annotate()` and `Sum()` to compute holdings at the database level
- Only fetch the aggregated results, not individual transactions

**Before (current):**
```python
buy_sell_txs = transactions.filter(type__in=POSITION_TRANSACTION_TYPES).order_by('date')
holdings = {}
for tx in buy_sell_txs:
    symbol = tx.symbol
    if symbol not in holdings:
        holdings[symbol] = {'shares': 0, 'cost_basis': Decimal('0')}
    if tx.type == 'BUY':
        holdings[symbol]['shares'] += tx.quantity
        holdings[symbol]['cost_basis'] += tx.amount
    elif tx.type == 'SELL':
        holdings[symbol]['shares'] -= tx.quantity
        holdings[symbol]['cost_basis'] -= tx.amount
```

**After:**
```python
from django.db.models import Sum, Case, When, F, DecimalField

holdings = Transaction.objects.filter(
    account=account,
    type__in=POSITION_TRANSACTION_TYPES
).values('symbol').annotate(
    shares=Sum(
        Case(
            When(type='BUY', then='quantity'),
            When(type='SELL', then=-F('quantity')),
            default=0,
            output_field=DecimalField()
        )
    ),
    cost_basis=Sum(
        Case(
            When(type='BUY', then='amount'),
            When(type='SELL', then=-F('amount')),
            default=0,
            output_field=DecimalField()
        )
    )
).filter(shares__gt=0).order_by('symbol')
```

#### 2b. Add pagination for transaction lists

- If the account has more than 100 transactions, paginate the results
- Use Django's built-in pagination or a simple offset-based approach

```python
from django.core.paginator import Paginator

transactions = Transaction.objects.filter(account=account).order_by('-date')
paginator = Paginator(transactions, 100)
page_number = request.GET.get('page', 1)
page_obj = paginator.get_page(page_number)
```

---

## Constraints

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- The database aggregation must handle NULL values gracefully
- All existing tests must continue to pass

---

## Files to Modify

1. `core/models.py` — Add `auto_create_or_get()` to `TransactionType`
2. `imports/parsers.py` — Update `_normalize_type()` to use the registry
3. `core/views_accounts.py` — Replace in-memory loop with database aggregation

---

## Tests

- Unit test for `auto_create_or_get()`: Test with existing code (should return existing), new code (should create)
- Unit test for database aggregation: Test with BUY/SELL transactions, verify correct net shares
- Unit test for pagination: Test with 150 transactions, verify page 1 has 100, page 2 has 50

---

## Manual Steps Required

- [ ] `python manage.py makemigrations` (if any model changes)
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature_rbc_pdf_fixes`
- [ ] Run full test suite: `python run_tests.py`

---

## Human Approval Required

Human approval required before any git push or DB changes.
