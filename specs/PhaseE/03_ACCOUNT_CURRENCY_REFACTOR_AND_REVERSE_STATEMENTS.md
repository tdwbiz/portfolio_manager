# Account Currency Refactor + Reverse Statement Generation

**Phase:** E (Data Cleaning)
**Created:** 2026-07-07

---

## Problem Statement

### 1. Account.currency is misleading

The `Account` model has a single `currency` CharField (`CAD` or `USD`, default `USD`). But **10 out of 13 accounts actually hold both CAD and USD positions/transactions**:

| Account | `account.currency` | Actual Transaction Currencies | RBC Monthly Currencies | Multi-Currency? |
|---------|--------------------|------------------------------|------------------------|-----------------|
| 12345678 | USD | (none yet) | CAD, USD | **Yes** |
| 90000004 | CAD | CAD, USD | CAD, USD | **Yes** |
| 90000006 | CAD | CAD, USD | CAD, USD | **Yes** |
| 90000007 | CAD | CAD, USD | CAD, USD | **Yes** |
| 90000001 | CAD | CAD only | CAD only | No |
| 90000002 | CAD | CAD, USD | CAD, USD | **Yes** |
| 90000008 | CAD | CAD only | CAD only | No |
| 90000005 | CAD | CAD, USD | CAD, USD | **Yes** |
| 90000003 | CAD | CAD, USD | CAD, USD | **Yes** |
| SUSAN IB | USD | CAD, USD | (none) | **Yes** |
| TOM ADVISOR | USD | USD only | (none) | No |
| TOM IB | USD | CAD, USD | (none) | **Yes** |
| TS JOINT | USD | CAD, USD | (none) | **Yes** |

The single `Account.currency` field is **wrong** for multi-currency accounts. It doesn't represent reality.

### 2. Starting balance is singular, not per-currency

`Account.starting_balance` and `Account.starting_balance_date` are also single values. For a multi-currency account, you need a starting balance **per currency** (e.g., CAD=$10,000, USD=$5,000).

### 3. Forward computation requires complete data from the start

The current `generate_monthly_statements()` walks forward from the earliest transaction month, computing ending MV = beginning MV + buys - sells + income. But if there are gaps in the transaction data (missing months/years), the cumulative calculation is wrong from that point forward.

### 4. RBC tables are reconciliation references only

`core_rbc*` tables exist solely to validate and fill CSV data. Once the CSV data is clean and verified, we won't re-reconcile old data again. The monthly/annual statements generated from CSV are the **authoritative records** going forward.

---

## Recommendation: Sub-Account (Currency Split) Model

### Proposed New Model

```python
class AccountCurrency(models.Model):
    """Sub-account representing one currency within an Account.

    An Account (e.g., 'TOM ADVISOR') may have multiple sub-accounts
    for different currencies (CAD, USD). Each sub-account has its own
    starting balance and tracks per-currency statements.

    RBC PDFs already use this pattern: RbcMonthly has (account FK, currency).
    CoreMonthlyStatement and CoreAnnualReport already use (account FK, currency).
    This refactor aligns the Account model with the rest of the system.
    """

    account = models.ForeignKey(
        "Account",
        on_delete=models.CASCADE,
        related_name="currencies",
    )
    currency = models.CharField(
        max_length=3,
        choices=[("CAD", "CAD"), ("USD", "USD")],
    )
    starting_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    starting_balance_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = [("account", "currency")]
        ordering = ["currency"]

    def __str__(self):
        return f"{self.account.account_id} ({self.currency})"
```

### What Changes

**Account model:**
- Remove `Account.currency` (deprecated, keep for backward compat with a data migration)
- Remove `Account.starting_balance` and `Account.starting_balance_date` (move to sub-account)
- Add `AccountCurrency` model (above)
- Keep `Account` as the "master" record: `account_id`, `name`, `broker`, `category`, `owner`

**ForeignKey references:**
- `Transaction.account` → stays on Account (transactions belong to the account, not a currency sub-account)
- `CoreMonthlyStatement.account` → stays on Account (the model already has a separate `currency` field)
- `CoreAnnualReport.account` → stays on Account (same, has separate `currency` field)
- `RbcMonthly.account` → stays on Account (has separate `currency` field)
- `CoreReconciliation.account` → stays on Account

**Key insight:** The `currency` dimension is **already handled separately** in `CoreMonthlyStatement`, `CoreAnnualReport`, `RbcMonthly`, `RbcAnnual`, and `Transaction`. The `Account.currency` field is the **only** place where a single currency is incorrectly stored at the account level.

### Data Migration Strategy

1. **Create migration:** Add `AccountCurrency` table
2. **Detect currencies per account:** Scan `Transaction` and `RbcMonthly` tables for distinct currencies per account
3. **Create sub-accounts:** For each (account, currency) pair with data, create an `AccountCurrency` record
4. **Compute starting balances:**
   - For each (account, currency): earliest transaction date → that's where data starts
   - For accounts with RBC data: use RBC beginning balance as the starting point
   - For accounts with both: prefer RBC if CSV has gaps
5. **Backfill `Account.starting_balance`:** Set to CAD starting balance (for backward compat during transition)
6. **Update forms/admin:** Add sub-account editor to Account admin
7. **Deprecate `Account.currency`:** Mark as deprecated, set to `CAD` for CAD-only accounts, `MIXED` for multi-currency

### UI Changes

**Account admin (`core/admin.py`):**
- Add `AccountCurrency` inlines (TabularInline)
- Allow adding/editing/deleting sub-accounts per account
- Show currency breakdown in `list_display`

**Account edit form:**
- Keep top-level fields: `account_id`, `name`, `broker`, `category`, `owner`
- Add "Currencies" section below with a table:
  - Currency | Starting Balance | Starting Date | Actions
  - [CAD] [$10,000.00] [2018-01-01] [Edit] [Delete]
  - [USD] [$5,000.00] [2018-01-01] [Edit] [Delete]
  - [+ Add Currency]

**Account detail page:**
- Show sub-account cards:
  - **CAD Sub-Account:** Starting $10,000 | Current ~$45,000
  - **USD Sub-Account:** Starting $5,000 | Current ~$23,000

---

## Reverse Statement Generation

### Algorithm

Once sub-accounts exist with known starting balances (or known end balances), generate statements going **backwards** from the latest date:

```
reverse_generate_monthly_statements(account, anchor_month, known_ending_cad, known_ending_usd=None):
    
    # 1. Get all transactions for the account, grouped by (month, currency)
    tx_by_month = {}  # {(year, month, currency): [transactions]}
    for tx in Transaction.objects.filter(account=account):
        ms = month_start(tx.date)
        key = (ms.year, ms.month, tx.currency)
        tx_by_month[key].append(tx)
    
    # 2. Generate anchor month statements first
    #    User provides: known_ending_cad, known_ending_usd
    #    Beginning of anchor month = ending of prior month (computed normally)
    
    # 3. Walk backwards through months
    current_month = anchor_month
    while True:
        # For each currency that has transactions
        for currency in ['CAD', 'USD']:
            if currency not in tx_by_month.get(current_month, {}):
                continue
            
            txs = tx_by_month[(current_month.year, current_month.month, currency)]
            
            # Compute this month's activity
            buys, sells, divs, interest, commission = sum_activities(txs)
            
            # This month's ending = known (or computed from prior)
            # This month's beginning = ending - sells + buys - divs - interest + commission
            beginning = current_ending - sells + buys - divs - interest + commission
            
            # Save CoreMonthlyStatement
            CoreMonthlyStatement.objects.update_or_create(
                account=account,
                statement_month=current_month,
                currency=currency,
                defaults={
                    'beginning_market_value': beginning,
                    'ending_market_value': current_ending,
                    'total_buys': buys,
                    'total_sells': sells,
                    ...
                }
            )
        
        # Move to prior month
        prev_month = current_month.replace(month=current_month.month - 1 or 12,
                                            year=current_month.year - (1 if current_month.month == 1 else 0))
        
        # Stop when no more transactions
        if prev_month not in tx_by_month:
            break
        current_month = prev_month
```

### UI Flow

```
Account: [TOM_LIF ▼]
Anchor Month: [2024-06-01 ▼]
  Ending Balance CAD: [45,123.45]
  Ending Balance USD: [23,456.78]  (optional)
[Generate Backwards]
```

1. Select account
2. Pick an anchor month (the month where you KNOW the ending balance)
3. Enter known ending balances per currency
4. System generates all monthly statements going backwards from anchor month
5. Statements are validated against RBC data at the far end

### Where does the known ending balance come from?

**Source 1: RBC PDF data** — `RbcMonthly.end_total_market_value` is the most reliable source for known ending values. For each account, find the latest RBC statement per currency and use that as the anchor.

**Source 2: Manual entry** — User manually verifies/edits the ending balance if RBC data is incomplete or incorrect.

**Source 3: Forward computation from earliest date** — If CSV data is complete from the start, the forward algorithm works fine. Use it to generate statements, then verify against RBC at the far end.

---

## Implementation Plan

### Phase E3-E: Account Currency Refactor

| Step | Task | Files | Effort |
|------|------|-------|--------|
| 1 | Create `AccountCurrency` model + migration | `core/models.py`, migration | 0.5h |
| 2 | Data migration: detect currencies, create sub-accounts, compute starting balances | migration file | 1h |
| 3 | Update `AccountForm` to include currency inlines | `core/forms.py` | 0.5h |
| 4 | Update `AccountAdmin` with inlines | `core/admin.py` | 0.5h |
| 5 | Update account templates (list, detail, edit) | `core/templates/core/*.html` | 1h |
| 6 | Update views (account_detail, accounts_list) | `core/views_accounts.py` | 0.5h |
| 7 | Update `account.currency` references across codebase | grep for `account.currency`, `Account.currency` | 0.5h |
| 8 | Tests | `core/tests/test_account_currency.py` | 0.5h |

### Phase E4: Reverse Statement Generation

| Step | Task | Files | Effort |
|------|------|-------|--------|
| 1 | `reverse_generate_monthly_statements()` utility | `core/utils/statements.py` | 1h |
| 2 | View + template for reverse generation | `core/views.py`, `reverse_statements.html` | 1h |
| 3 | Auto-detect anchor months from RBC data | `core/utils/reconciliation.py` (helper) | 0.5h |
| 4 | Tests | `core/tests/test_reverse_statements.py` | 0.5h |

### Total Effort: ~8 hours (1 day)

---

## Future State

Once data is clean:

1. **Monthly/annual statements** are generated from CSV transactions (forward or reverse as needed)
2. **Reconciliation** runs annually on the latest year only (not re-reconciling old data)
3. **RBC tables** remain as reference data but are no longer the source of truth
4. **Account.currency** field is deprecated — all currency-aware operations use the `currency` field on `CoreMonthlyStatement`, `CoreAnnualReport`, `Transaction`, etc.

---

## Key Design Decisions

1. **Don't change FK relationships** — `Transaction.account`, `CoreMonthlyStatement.account`, etc. all stay on `Account`. The `currency` dimension is already a separate field on those models. Only the `Account` model's own `currency` field is problematic.

2. **Don't remove `Account.currency` immediately** — keep it as deprecated for backward compatibility during transition. Data migration sets it to the dominant currency or `MIXED`.

3. **Starting balance per sub-account** — each (account, currency) pair gets its own starting balance. This is the single authoritative value for reverse computation.

4. **RBC as anchor source** — RBC monthly statements provide the most reliable "known ending balance" values. Use them as anchor points for reverse generation.
