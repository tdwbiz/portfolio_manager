# Phase E: Transaction Reconciliation and Statement Generation

**Goal:** Use CSV-imported transactions (`core_transaction`) as the master source, generate monthly and annual statements from it, then reconcile against RBC PDF-imported data (`core_rbc_monthly`, `core_rbc_monthly_balance`, `core_rbc_monthly_transaction`, `core_rbc_annual`) to fill gaps and maximize data coverage.

---

## Problem Statement

- **CSV imports** → `core_transaction` — accurate, reliable, comprehensive
- **PDF imports** → `core_rbc_monthly`, `core_rbc_monthly_balance`, `core_rbc_monthly_transaction`, `core_rbc_annual` — hit-and-miss accuracy
- **Goal:** Make CSV the master source, use RBC PDFs for validation and gap-filling

---

## Overview

This spec has **three phases** that build on each other:

1. **E3-A:** Gap analysis report — show earliest data per account
2. **E3-B:** Generate monthly statements from transactions
3. **E3-C:** Generate annual reports from transactions
4. **E3-D:** Reconciliation between CSV, generated statements, and RBC PDFs

---

## E3-A: Gap Analysis Report

### Problem
We need to know, for each account, how complete our transaction data is.

### Output: Report showing per account

| Field | Description |
|-------|-------------|
| `account_id` | Account identifier |
| `earliest_csv_date` | Earliest transaction date in `core_transaction` |
| `earliest_continuous_start` | Start of the longest continuous date range in transactions |
| `earliest_continuous_end` | End of that continuous range |
| `earliest_rbc_date` | Earliest date in `core_rbc_monthly` for this account |
| `earliest_rbc_monthly_tx_date` | Earliest date in `core_rbc_monthly_transaction` |
| `earliest_rbc_annual_date` | Earliest date in `core_rbc_annual` |
| `days_missing_continuous` | Number of calendar days missing between `earliest_continuous_start` and `earliest_continuous_end` where no transactions exist |
| `total_months_gap` | Total number of months with zero transactions |

### How to compute

```python
def account_gap_analysis(account):
    """For a given Account, return completeness metrics."""
    
    # 1. Get all transaction dates for this account
    tx_dates = Transaction.objects.filter(account=account).values_list('date', flat=True).distinct().order_by('date')
    
    # 2. Find earliest continuous run
    continuous_run = find_longest_continuous_run(tx_dates)
    
    # 3. Find all gaps within that range
    gaps = find_gaps_in_range(continuous_run.start, continuous_run.end, tx_dates)
    
    # 4. Get RBC PDF data dates
    rbc_monthly_dates = RbcMonthly.objects.filter(account=account).values_list('statement_date', flat=True)
    rbc_tx_dates = RbcMonthlyTransaction.objects.filter(rbc_monthly__account=account).values_list('date', flat=True)
    rbc_annual_dates = RbcAnnual.objects.filter(account=account).values_list('report_year', flat=True)
    
    return {
        'account': account,
        'earliest_csv_date': tx_dates.first(),
        'earliest_continuous_start': continuous_run.start,
        'earliest_continuous_end': continuous_run.end,
        'days_missing_continuous': len(gaps),  # or sum of gap widths
        'total_months_with_transactions': count_months(tx_dates),
        'total_months_without_transactions': count_empty_months(tx_dates),
        'earliest_rbc_statement': rbc_monthly_dates.min(),
        'earliest_rbc_transaction': rbc_tx_dates.min(),
        'earliest_rbc_annual': rbc_annual_dates.min(),
        'csv_coverage_pct': coverage_percentage(tx_dates),
    }
```

### Where to show

**New view:** `core/views.py` → `gap_analysis()` — renders a table
**New template:** `core/templates/core/gap_analysis.html`
**URL:** `/accounts/gap-analysis/`

---

## E3-B: Monthly Statement Generation

### Problem
We have raw transactions but no monthly summaries. RBC PDFs provide monthly summaries — we need to generate them from our CSV master data.

### New Model: `core_monthly_statements`

```python
class CoreMonthlyStatement(models.Model):
    """Monthly statement generated from core_transaction data.
    
    Replaces RBC monthly statement data when CSV is available and complete.
    One row per (account, month, currency).
    """
    
    account = models.ForeignKey("Account", on_delete=models.CASCADE, related_name="core_monthly_statements")
    statement_month = models.DateField(help_text="First day of the month")
    currency = models.CharField(max_length=3, default="CAD", choices=[("CAD", "CAD"), ("USD", "USD")])
    
    # Portfolio value metrics
    beginning_market_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    ending_market_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Transaction totals for the month
    total_buys = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_sells = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_dividends = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_interest = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_commission = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Derived
    net_flow = models.DecimalField(max_digits=15, decimal_places=2, default=0)  # buys - sells
    total_transactions = models.IntegerField(default=0)
    
    # Tracking
    generated_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = [("account", "statement_month", "currency")]
        ordering = ["-statement_month"]
        indexes = [
            models.Index(fields=["account", "statement_month"]),
        ]
    
    def __str__(self):
        return f"{self.account} — {self.statement_month.strftime('%Y-%m')} [{self.currency}]"
```

### Generation Logic

```python
def generate_monthly_statements(account, months_to_generate=None):
    """Generate monthly statements from transactions for given account.
    
    For each month:
    1. Get all transactions for that month
    2. Compute beginning market value (ending value of previous month)
    3. Sum buys, sells, dividends, interest, commissions
    4. Compute ending market value
    5. Store in CoreMonthlyStatement
    """
```

### Where to run

**New management command:** `python manage.py generate_monthly_statements --account=TEST001 --from=2020-01-01`

**New view:** `core/views.py` → `generate_monthly_statements()` — UI to trigger generation
**New template:** `core/templates/core/generate_statements.html`
**URL:** `/accounts/generate-monthly-statements/`

---

## E3-C: Annual Report Generation

### Problem
Similarly, we need annual reports from transactions, not just RBC PDFs.

### New Model: `core_annual_reports`

```python
class CoreAnnualReport(models.Model):
    """Annual report generated from core_transaction data.
    
    Replaces RBC annual report data when CSV is available and complete.
    One row per (account, year, currency).
    """
    
    account = models.ForeignKey("Account", on_delete=models.CASCADE, related_name="core_annual_reports")
    report_year = models.IntegerField(help_text="Calendar year")
    currency = models.CharField(max_length=3, default="CAD", choices=[("CAD", "CAD"), ("USD", "USD")])
    
    # Balance metrics
    beginning_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    ending_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    deposits = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    withdrawals = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    net_investment_return = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    amount_invested = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Summary totals
    total_buys = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_sells = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_income = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_commission = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_transactions = models.IntegerField(default=0)
    
    generated_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = [("account", "report_year", "currency")]
        ordering = ["-report_year"]
        indexes = [
            models.Index(fields=["account", "report_year"]),
        ]
    
    def __str__(self):
        return f"{self.account} — {self.report_year} [{self.currency}]"
```

### Generation Logic

```python
def generate_annual_reports(account, years_to_generate=None):
    """Generate annual reports from transactions for given account.
    
    Uses CoreMonthlyStatement as intermediate, or falls back to raw transactions.
    """
```

### Where to run

**New management command:** `python manage.py generate_annual_reports --account=TEST001 --from=2020`

**New view:** `core/views.py` → `generate_annual_reports()`
**New template:** `core/templates/core/generate_annual.html`
**URL:** `/accounts/generate-annual-reports/`

---

## E3-D: Reconciliation Engine

### Problem
After generating monthly and annual statements from CSV data, we need to reconcile against RBC PDF data to:
1. Validate accuracy
2. Identify discrepancies
3. Fill gaps where CSV is missing but RBC has data

### New Model: `core_reconciliation`

```python
class CoreReconciliation(models.Model):
    """Reconciliation result between CSV-generated and RBC PDF data.
    
    Tracks matches and discrepancies for each account/month/year.
    """
    
    account = models.ForeignKey("Account", on_delete=models.CASCADE)
    reconciliation_type = models.CharField(max_length=20, choices=[
        ("monthly", "Monthly"),
        ("annual", "Annual"),
    ])
    period_start = models.DateField(help_text="Start of period")
    period_end = models.DateField(help_text="End of period")
    
    # CSV values
    csv_beginning_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_ending_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_buys = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_sells = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_income = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    csv_total_transactions = models.IntegerField(null=True)
    
    # RBC values
    rbc_beginning_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_ending_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_buys = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_sells = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_income = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    rbc_total_transactions = models.IntegerField(null=True)
    
    # Discrepancy tracking
    matching = models.BooleanField(default=False)
    discrepancy_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    discrepancy_notes = models.TextField(blank=True, default="")
    
    reconciled_at = models.DateTimeField(auto_now_add=True)
    reconciled_by = models.CharField(max_length=100, blank=True, default="auto")
    
    class Meta:
        unique_together = [("account", "reconciliation_type", "period_start", "period_end")]
        ordering = ["-period_start"]
        indexes = [
            models.Index(fields=["account", "reconciliation_type"]),
        ]
    
    def __str__(self):
        return f"{self.account} {self.reconciliation_type} {self.period_start}"
```

### Reconciliation Logic

```python
def reconcile_account(account, period_type="monthly", months=None):
    """Reconcile CSV-generated vs RBC PDF data for an account.
    
    For each month:
    1. Load CSV-generated monthly statement
    2. Load RBC monthly statement
    3. Compare beginning/ending values, transaction totals
    4. Record matches and discrepancies
    5. Flag gaps where one source has data but not the other
    """
```

### Gap Filling Strategy

After reconciliation:
- If CSV has a gap but RBC has data → propose importing RBC transactions into `core_transaction`
- If RBC has a gap but CSV has data → flag for review
- If both have data but values differ → flag as discrepancy requiring manual review

---

## File Changes

| File | Changes |
|------|---------|
| `core/models.py` | Add `CoreMonthlyStatement`, `CoreAnnualReport`, `CoreReconciliation` models |
| `core/admin.py` | Register new models |
| `core/views.py` | Add views: `gap_analysis()`, `generate_monthly_statements()`, `generate_annual_reports()`, `reconcile_account()` |
| `core/views.py` (or new file) | Add `reconciliation` view group |
| `core/urls.py` | Add URL routes |
| `core/management/commands/` | Add `generate_monthly_statements.py`, `generate_annual_reports.py`, `reconcile.py` |
| `core/utils/statements.py` | New file — generation logic |
| `core/utils/reconciliation.py` | New file — reconciliation logic |
| `core/templates/core/` | Add `gap_analysis.html`, `generate_statements.html`, `generate_annual.html`, `reconciliation.html` |
| `core/tests/` | New test file `test_reconciliation.py` |

---

## Migration Impact

- **E3-A:** No migration (report only, reads existing data)
- **E3-B:** Yes — `CoreMonthlyStatement` model
- **E3-C:** Yes — `CoreAnnualReport` model
- **E3-D:** Yes — `CoreReconciliation` model

---

## Test Plan

| Test | What it covers |
|------|---------------|
| `test_gap_analysis_empty_account` | Empty account returns zero coverage |
| `test_gap_analysis_with_gaps` | Account with missing months shows correct gap count |
| `test_gap_analysis_continuous_run` | Correct longest continuous run |
| `test_generate_monthly_statement` | Single month computed correctly |
| `test_generate_monthly_statement_multi_month` | Multi-month chain works |
| `test_generate_annual_report` | Year computed from monthly statements |
| `test_reconciliation_match` | CSV and RBC agree → matching=True |
| `test_reconciliation_discrepancy` | CSV and RBC differ → matching=False with amount |
| `test_reconciliation_rbc_only` | RBC has data, CSV doesn't → gap flagged |
| `test_reconciliation_csv_only` | CSV has data, RBC doesn't → no RBC data flagged |

**~10 new tests**

---

## Success Criteria

1. Gap analysis report shows earliest data per account with missing day counts
2. Monthly statements can be generated from transactions
3. Annual reports can be generated from monthly statements
4. Reconciliation identifies matches and discrepancies between CSV and RBC data
5. Gaps in CSV where RBC has data are flagged for import
6. All existing 122 tests still pass
7. ~10 new tests pass

---

## Implementation Order

Recommended: **E3-A → E3-B → E3-C → E3-D**

- E3-A is quick (report only, no new models)
- E3-B and E3-C are similar (generate statements from transactions)
- E3-D is the most complex (needs both E3-B and E3-C outputs)

---

## Notes

- Monthly statements should be generated per account, per currency (CAD/USD)
- Annual reports should aggregate from monthly statements, not raw transactions
- RBC PDF reconciliation should compare like-for-like: monthly vs monthly, annual vs annual
- Discrepancies > $1.00 should be flagged as requiring manual review
- The reconciliation should be idempotent (running twice produces same results)
- Management commands allow batch processing (all accounts, all years)
- Admin UI allows manual review and resolution of discrepancies
