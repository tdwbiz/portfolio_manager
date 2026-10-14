# Feature Spec: RBC Annual Report Import

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/rbc-annual-report-import

---

## Context

Specs 01–21 are complete. The application already supports:
- CSV statement import (`imports/parsers.py::parse_rbc_csv`)
- PDF statement import via `imports/parsers_pdf.py::parse_rbc_pdf` (Spec 21)
- Models: `Transaction`, `BrokerStatement`, `PdfStatement`, `PdfAccountBalance`, `PdfTransaction`
- Management command: `manage_pdf_import` for PDF statements
- Import history UI and portfolio summary with account categories

RBC also provides **Annual Reports** — separate PDF documents delivered alongside monthly statements. These are distinct from statement PDFs and contain different data:

**Annual Report structure** (based on 4 sample files in the specs folder):
- `Annual Report-2259 2025-12-31.pdf` — 6 pages, RIF account 900-00005
- `Annual Report-4033 2025-12-31.pdf` — 4 pages, LIF account 900-00008
- `Annual Report-5362 2025-12-31.pdf` — 6 pages, RRSP account 900-00003
- `Annual Report-7620 2025-12-31.pdf` — 6 pages, IF account 690-7762

Each annual report PDF contains **six data sections** organized by report type and currency:

**Annual Investment Performance Report** (pages 1–3):
1. **Balances — CAD** (page 1): Change in account value table (beginning value, deposits, withdrawals, net return, ending value)
2. **Balances — USD** (page 2): Same change in account value table in USD
3. **Performance — CAD** (page 2): Money-weighted rate of return at 1yr, 3yr, 5yr, 10yr, since-date horizons
4. **Performance — USD** (page 3): Same returns in USD

**Annual Charges and Compensation Report** (pages 4–6):
5. **Charges — CAD** (page 4): Operating charges, transaction charges, commissions paid by you
6. **Charges + Compensation — USD** (pages 4–5): Same charges plus third-party compensation (trailing commissions, etc.) in USD

Some accounts (e.g. 4033 LIF) have only a CAD section (4-page PDF). Others have both CAD and USD (6-page PDF).

The annual report PDFs have a **different layout** from statement PDFs. Text extraction produces wide character spacing that must be cleaned with regex before parsing. The PDFs are 4–6 pages depending on account type.

This spec adds a **separate annual report import pipeline** with RBC-specific models (`RbcAnnualReport`, `RbcAnnualBalances`, `RbcAnnualPerformance`, `RbcAnnualCharges`, `RbcAnnualCompensation`) alongside the existing models. Annual report data does NOT replace statement or CSV data — all three coexist. The UI will show annual reports in a dedicated section.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- PDF parsing: PyMuPDF (`fitz`) — already installed (v1.27.2.3)
- Frontend: Bootstrap 5.3.2 (CDN)
- Text cleaning: Python `re` module (required for RBC PDF character spacing)

---

## Requirements

### 1. Create new Django models

Add the following models to `core/models.py` (after `PdfTransaction`, before `FileImportLog`). All models are prefixed with `RbcAnnual` to clearly distinguish RBC annual report data from other broker reports (e.g., Interactive Brokers, which has a completely different structure and would use different models in a future spec).

```python
class RbcAnnualReport(models.Model):
    """A parsed RBC Annual Report PDF — one per PDF file.

    This model is RBC-specific. Other brokers (e.g., IB) have different
    report structures and would use separate models in future specs.
    """
    BROKER_CHOICES = [('RBC', 'Royal Bank of Canada')]

    account = models.ForeignKey(
        'Account',
        on_delete=models.CASCADE,
        related_name='rbc_annual_reports',
    )
    broker = models.CharField(
        max_length=10, choices=BROKER_CHOICES, default='RBC',
        help_text='Broker name — always RBC for annual reports',
    )
    file_name = models.CharField(max_length=255)
    report_year = models.IntegerField(help_text='Calendar year the report covers')
    report_period_start = models.DateField(
        help_text='Start of reporting period, e.g. January 1, 2025',
    )
    report_period_end = models.DateField(
        help_text='End of reporting period, e.g. December 31, 2025',
    )
    account_number = models.CharField(
        max_length=50, blank=True, default='',
        help_text='RBC account number from PDF (e.g. 900-00005)',
    )
    account_type_code = models.CharField(
        max_length=10, blank=True, default='',
        help_text='RBC account type code from PDF (e.g. RIF, LIF, RRSP, IF)',
    )
    parsed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-report_year', 'account']
        unique_together = [('account', 'report_year')]

    def __str__(self):
        return f"RBC Annual Report {self.account} — {self.report_year}"


class RbcAnnualBalances(models.Model):
    """Change in account value table from the RBC Annual Investment Performance Report.

    One record per currency (CAD and/or USD) per annual report.
    Captures the "Balances" section: beginning value, deposits, withdrawals,
    net investment return, ending value, and amount invested.
    """
    rbc_annual_report = models.ForeignKey(
        RbcAnnualReport,
        on_delete=models.CASCADE,
        related_name='balance_snapshots',
    )
    currency = models.CharField(
        max_length=3, default='CAD',
        help_text='CAD or USD — each account may have both currency sections',
    )
    beginning_market_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Market value at start of period',
    )
    deposits_transfers_in = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Total deposits and transfers into the account',
    )
    withdrawals_transfers_out = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Total withdrawals and transfers out of the account',
    )
    net_investment_return = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Net investment return (gains/losses + income) for the period',
    )
    ending_market_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Market value at end of period',
    )
    amount_invested = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Beginning value + deposits - withdrawals (total invested capital)',
    )

    class Meta:
        ordering = ['currency']
        unique_together = [('rbc_annual_report', 'currency')]

    def __str__(self):
        return f"RBC Balances {self.rbc_annual_report} — {self.currency}"


class RbcAnnualPerformance(models.Model):
    """Money-weighted rate of return at various time horizons from the RBC Annual Investment Performance Report.

    One record per currency (CAD and/or USD) per annual report.
    Captures the "Performance" section: returns at 1yr, 3yr, 5yr, 10yr, since-date.
    """
    rbc_annual_report = models.ForeignKey(
        RbcAnnualReport,
        on_delete=models.CASCADE,
        related_name='performance_records',
    )
    currency = models.CharField(
        max_length=3, default='CAD',
        help_text='CAD or USD — each account may have both currency sections',
    )
    since_date = models.DateField(
        null=True, blank=True,
        help_text='Reference date for "since" returns (e.g. Jan 1, 2016)',
    )

    class Meta:
        ordering = ['currency']
        unique_together = [('rbc_annual_report', 'currency')]

    def __str__(self):
        return f"RBC Performance {self.rbc_annual_report} — {self.currency}"


class RbcAnnualPerformanceReturn(models.Model):
    """A single money-weighted return entry for one time horizon.

    Child of RbcAnnualPerformance. One record per period_label (1_year, 3_years, etc.).
    """
    performance = models.ForeignKey(
        RbcAnnualPerformance,
        on_delete=models.CASCADE,
        related_name='returns',
    )
    period_label = models.CharField(
        max_length=20,
        help_text='Time horizon: "1_year", "3_years", "5_years", "10_years", "since_date"',
    )
    return_pct = models.DecimalField(
        max_digits=8, decimal_places=4,
        help_text='Annualized money-weighted return as a percentage (e.g. 15.93 for 15.93%)',
    )

    class Meta:
        ordering = ['period_label']
        unique_together = [('performance', 'period_label')]

    def __str__(self):
        return f"{self.performance} — {self.period_label}: {self.return_pct}%"


class RbcAnnualCharges(models.Model):
    """Fee and compensation data from the RBC Annual Charges and Compensation Report.

    One record per charge category per currency (CAD and/or USD) per annual report.
    Captures operating charges, transaction charges, commissions, and total paid by you.
    """
    rbc_annual_report = models.ForeignKey(
        RbcAnnualReport,
        on_delete=models.CASCADE,
        related_name='charge_records',
    )
    currency = models.CharField(
        max_length=3, default='CAD',
        help_text='CAD or USD — fees may be reported in both currencies',
    )
    charge_category = models.CharField(
        max_length=80,
        help_text='e.g. "Operating Charges", "Commissions - Equities/options", "Total transaction charges"',
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal('0.0000'),
        help_text='Amount in the reported currency',
    )
    is_subtotal = models.BooleanField(
        default=False,
        help_text='True for subtotal rows (e.g. "Total transaction charges"), False for line items',
    )

    class Meta:
        ordering = ['currency', 'charge_category']
        unique_together = [('rbc_annual_report', 'currency', 'charge_category')]

    def __str__(self):
        return f"RBC Charges {self.rbc_annual_report} — {self.currency}: {self.charge_category} = ${self.amount}"


class RbcAnnualCompensation(models.Model):
    """Third-party compensation received by RBC from the Annual Charges and Compensation Report.

    One record per compensation type per currency (CAD and/or USD) per annual report.
    Captures trailing commissions, other compensation, and total from third parties.
    """
    rbc_annual_report = models.ForeignKey(
        RbcAnnualReport,
        on_delete=models.CASCADE,
        related_name='compensation_records',
    )
    currency = models.CharField(max_length=3, default='CAD')
    compensation_type = models.CharField(
        max_length=80,
        help_text='e.g. "Trailing commission", "Other compensation", "Total compensation from third parties"',
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal('0.0000'),
        help_text='Amount in the reported currency',
    )

    class Meta:
        ordering = ['currency', 'compensation_type']
        unique_together = [('rbc_annual_report', 'currency', 'compensation_type')]

    def __str__(self):
        return f"RBC Compensation {self.rbc_annual_report} — {self.currency}: {self.compensation_type} = ${self.amount}"
```

**Model design notes:**
- All models are prefixed with `RbcAnnual` to clearly identify them as RBC-specific. Other brokers (e.g., Interactive Brokers) have completely different report structures and would use separate models.
- `RbcAnnualReport` is the parent record — one per PDF file, linked to an `Account`. The `broker` field is always "RBC".
- `RbcAnnualBalances` captures the "Balances" section (Change in Account Value table) per currency (CAD and/or USD).
- `RbcAnnualPerformance` captures the "Performance" section header per currency, with `RbcAnnualPerformanceReturn` child records for each time horizon (1yr, 3yr, 5yr, 10yr, since-date).
- `RbcAnnualCharges` captures all fee line items from the Charges report (operating charges, transaction charges, commissions) per currency.
- `RbcAnnualCompensation` captures compensation RBC received from third parties (trailing commissions, etc.) per currency.
- Annual report models are **separate** from `PdfStatement`, `PdfTransaction`, `Transaction` — they do NOT replace any existing data.
- `unique_together` on `(account, report_year)` prevents duplicate imports for the same account/year.
- `currency` fields on child models allow accounts with both CAD and USD sections to be captured.

### 2. Create annual report parser: `imports/parsers_annual.py`

Create a new file `imports/parsers_annual.py` with a single public function:

```python
def parse_rbc_annual_report(file_path: str) -> dict:
    """Parse an RBC Annual Report PDF and return structured data.

    Returns:
        {
            'broker': str,  # Always 'RBC'
            'report_year': int,
            'report_period_start': date,
            'report_period_end': date,
            'account_number': str,
            'account_type_code': str,  # e.g. 'RIF', 'LIF', 'RRSP', 'IF'
            'balances': [
                {
                    'currency': str,
                    'beginning_market_value': Decimal,
                    'deposits_transfers_in': Decimal,
                    'withdrawals_transfers_out': Decimal,
                    'net_investment_return': Decimal,
                    'ending_market_value': Decimal,
                    'amount_invested': Decimal,
                },
                ...
            ],
            'performance': [
                {
                    'currency': str,
                    'since_date': date | None,
                    'returns': [
                        {'period_label': str, 'return_pct': Decimal},
                        ...
                    ],
                },
                ...
            ],
            'charges': [
                {'currency': str, 'category': str, 'amount': Decimal, 'is_subtotal': bool},
                ...
            ],
            'compensation': [
                {'currency': str, 'type': str, 'amount': Decimal},
                ...
            ],
        }
    """
```

**Parsing strategy — text extraction + cleaning + regex:**

RBC annual report PDFs have **wide character spacing** that produces output like:
```
A n n u a l   i n v e s t m e n t
```
This must be cleaned before any regex matching. Use this cleaning function:

```python
def _clean_rbc_text(text: str) -> str:
    """Clean RBC PDF text by removing excessive inter-character whitespace.

    RBC PDFs use wide character spacing, producing output like:
        'A n n u a l   i n v e s t m e n t'

    This function collapses runs of single-character + space patterns
    back to normal words, then normalizes remaining whitespace.
    """
    # Collapse 'X ' patterns (single char followed by space) when part of a word
    cleaned = re.sub(r'([a-zA-Z])\s+([a-zA-Z])', r'\1\2', text)
    # Normalize multiple spaces to single space and strip
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned
```

Apply `_clean_rbc_text()` to the full extracted text before parsing.

**Page structure detection:**

After cleaning, detect which report section each page belongs to:

```python
PERFORMANCE_HEADER = re.compile(r'Annualinvestmentperformancereport', re.IGNORECASE)
CHARGES_HEADER = re.compile(r'Annualchargesandcompensationreport', re.IGNORECASE)
```

The first occurrence of either header marks the start of that section. Pages before `CHARGES_HEADER` belong to the Performance Report (Balances + Performance sections). Pages from `CHARGES_HEADER` onward belong to the Charges Report (Charges + Compensation sections).

**Account metadata extraction (from Page 1):**

```python
# Account number: "Account 900- 00 0 05" → "900-00005"
acct_match = re.search(r'Account\s+(\d+)\s*[\s-]*(\d+)', cleaned_text)
account_number = f"{acct_match.group(1)}-{acct_match.group(2)}" if acct_match else ''

# Account type: "E L S I E S M A R A I S R R I F Account" → "RIF"
acct_type_match = re.search(r'([A-Z]{2,4})\s+Account', cleaned_text)
account_type_code = acct_type_match.group(1) if acct_type_code else ''

# Period: "from January 1, 2025 to December 31, 2025"
period_match = re.search(
    r'from\s+(\w+\s+\d+,\s+\d+)\s+to\s+(\w+\s+\d+,\s+\d+)',
    cleaned_text, re.IGNORECASE
)
```

**Performance Report parsing (pages 1–3):**

The Performance Report contains **two currency sections**: "Your Canadian dollar account" and "Your U.S. dollar account". Each currency section has two subsections:

1. **Balances subsection** (Change in Account Value table):
   - Beginning market value
   - Deposits and transfers-in
   - Withdrawals and transfers-out
   - Net investment return
   - Ending market value
   - Amount invested

   Parse by finding the currency header (`r'Your\s+(Canadian|U\.?\s*S\.?\s*)dollaraccount'`), then reading the table rows. Values are on the lines following each label.

2. **Performance subsection** (Money-Weighted Rate of Return table):
   - Columns: Past 1 year, Past 3 years, Past 5 years, Past 10 years, Since [date]
   - Values are percentages (e.g. "20.37%", "15.93%")

   Parse by finding the "Money-weighted rate of return" header, then reading the percentage values under each column header.

**Charges Report parsing (pages 4–6):**

The Charges Report has two currency sections: "Paid by you (CAD)" and "Paid by you (USD)". Each section contains two subsections:

1. **Charges subsection** (fees paid by you):
   - Operating Charges (usually "$0" or "—" for direct investing accounts)
   - Transaction Charges with sub-categories: "Commissions — Equities/options", etc.
   - Total transaction charges
   - Total paid by you

2. **Compensation subsection** (compensation RBC received from third parties):
   - Trailing commission
   - Other compensation (if any)
   - Total compensation from third parties

Parse by finding each currency header, then reading the charge/compensation lines. Use `is_subtotal=True` for rows labeled "Total".

**Date parsing helper:**

```python
MONTH_MAP = {
    'JANUARY': 1, 'FEBRUARY': 2, 'MARCH': 3, 'APRIL': 4,
    'MAY': 5, 'JUNE': 6, 'JULY': 7, 'AUGUST': 8,
    'SEPTEMBER': 9, 'OCTOBER': 10, 'NOVEMBER': 11, 'DECEMBER': 12,
}

def _parse_full_date(month_str: str, year: int) -> date:
    """Parse a full month name like 'January 1, 2025'."""
    month_str = month_str.strip().upper()
    for name, num in MONTH_MAP.items():
        if month_str.startswith(name):
            day_match = re.search(r'\d+', month_str)
            day = int(day_match.group()) if day_match else 1
            return date(year, num, day)
    raise ValueError(f"Cannot parse month: {month_str}")
```

**Error handling:**
- Replace bare `except Exception:` with `except Exception as e:` and `logger.warning()`.
- If a section is missing (e.g. no USD account), return empty list for that section — do NOT raise.
- If the PDF cannot be parsed at all, raise `ValueError("Unable to parse RBC Annual Report: <reason>")`.
- If character cleaning produces unreadable text, log a warning and attempt fallback parsing.

### 3. Create management command: `manage_annual_report_import`

Create `core/management/commands/manage_annual_report_import.py`. This command:

1. Accepts a directory path or file path as argument
2. For each `Annual Report-*.pdf` file found:
   a. Parse with `parse_rbc_annual_report()`
   b. Look up or create the `Account` (match by account number from PDF, or prompt)
   c. Check for duplicate `RbcAnnualReport` (same account + report_year) — skip if exists
   d. Create `RbcAnnualReport` record (broker='RBC')
   e. Create `RbcAnnualBalances` records (one per currency: CAD, USD)
   f. Create `RbcAnnualPerformance` records (one per currency: CAD, USD)
   g. Create `RbcAnnualPerformanceReturn` records for each time horizon under each performance record
   h. Create `RbcAnnualCharges` records for each fee line item
   i. Create `RbcAnnualCompensation` records
   j. Log summary: files processed, reports created, balances, performance records, returns, charges, compensation records

```python
# Command usage
python manage.py manage_annual_report_import /path/to/annual/reports/folder
python manage.py manage_annual_report_import /path/to/single/file.pdf
python manage.py manage_annual_report_import --dry-run /path/to/folder
```

**Dry-run mode:** Parse and display what would be imported without writing to DB.

**Duplicate handling:** If an `RbcAnnualReport` with the same account + report_year already exists, skip the file and log a warning. Do NOT overwrite existing data.

**File naming convention:** Annual report PDFs follow the pattern `Annual Report-<last4-account>-<YYYY-MM-DD>.pdf`. Extract account number from filename as a fallback if not found in PDF text.

### 4. Create import view and template

Add a new view and template for annual report imports:

**View: `imports/views.py` — `annual_report_import_view`**
- GET: Show upload form + list of existing annual reports
- POST: Handle file upload, parse PDF, create records, show results

**Template: `imports/templates/core/annual_report_import.html`**
- File upload area (drag-and-drop or file picker)
- Results section showing parsed data:
  - Report year, account number, account type code
  - **Performance Summary** (tabular):
    - Per currency: beginning value, deposits, withdrawals, net return, ending value, amount invested
    - Money-weighted returns at each time horizon (1yr, 3yr, 5yr, 10yr, since)
  - **Charges Summary** (tabular):
    - Per currency: operating charges, transaction charges (with sub-categories), total paid
  - **Third-Party Compensation** (tabular):
    - Per currency: trailing commissions, other compensation, total

### 5. Add annual report view to portfolio section

Add a new view `core/views.py` — `annual_reports_view`:

- Query all `AnnualReport` records for the user's accounts
- For each report, annotate with:
  - Total charges (sum of `ChargeRecord` amounts in CAD)
  - Total third-party compensation (sum in CAD)
  - Net investment return (from `PerformanceSnapshot` in CAD)
  - Ending market value (from `PerformanceSnapshot` in CAD)
- Render in `core/templates/core/annual_reports.html`:
  - Table: Year | Account | Ending Value | Net Return | Total Charges | Actions
  - Clicking a row expands to show full performance and charges detail
  - Bootstrap accordion or modal for detail view

**URL pattern:** `core/urls.py` — `path('annual-reports/', views.annual_reports_view, name='annual_reports')`

### 6. Add navigation link

Add an "Annual Reports" nav link in `core/templates/core/base.html` alongside existing navigation items (Dashboard, Portfolio, Import, Accounts). Place it after "Import" and before "Accounts".

---

## Tests — `imports/tests/test_annual_report_parser.py`

Add the following test methods:

- `test_parse_annual_report_2259`: Parse `Annual Report-2259 2025-12-31.pdf`; assert:
  - `report_year == 2025`
  - `account_number == '900-00005'`
  - `account_type_code == 'RIF'`
  - `balances` has 2 entries (CAD and USD)
  - CAD `ending_market_value == Decimal('105290.34')`
  - CAD `performance` returns includes `{'period_label': '1_year', 'return_pct': Decimal('20.37')}`
  - `charges` includes `{'category': 'Total transaction charges', 'amount': Decimal('49.75'), 'currency': 'CAD'}`
  - `compensation` includes `{'type': 'Trailing commission', 'amount': Decimal('6.42'), 'currency': 'CAD'}`

- `test_parse_annual_report_4033`: Parse `Annual Report-4033 2025-12-31.pdf`; assert:
  - `account_type_code == 'LIF'`
  - `balances` has 1 entry (CAD only — no USD section)
  - CAD `ending_market_value == Decimal('205722.34')`
  - CAD `performance` returns includes `{'period_label': '1_year', 'return_pct': Decimal('18.72')}`

- `test_text_cleaning`: Pass text with wide character spacing to `_clean_rbc_text()`; assert output has no excessive whitespace and readable words.

- `test_duplicate_annual_report_skipped`: Create an `RbcAnnualReport` for account + year 2025; call management command with same file; assert file is skipped and warning is logged.

- `test_annual_report_page_shows_summary`: GET `/annual-reports/`; assert response contains "RBC Annual Reports" and shows at least one report row.

Run with: `python manage.py test imports --settings=portfolio_manager.settings_test`

---

## Constraints

- Do NOT change or remove any existing models, views, or templates — only add new ones.
- Do NOT run migrations — that is a manual step.
- Do NOT push to git — list it under Manual Steps Required.
- Follow PEP8, add type hints to all new functions.
- Text cleaning (`_clean_rbc_text`) is **mandatory** — never parse raw extracted text from RBC annual report PDFs.
- Annual report models are **separate** from all existing import models — no cross-references except through `Account`.
- All models use `RbcAnnual` prefix to clearly distinguish RBC annual report data from other broker reports (e.g., Interactive Brokers).
- If a PDF has only 4 pages (like 4033), handle gracefully — do not assume 6 pages.
- Use `Decimal` for all monetary values and percentages.
- Add `# type: ignore[index]` on config dictionary access in any YAML-based code.

---

## Manual Steps Required

Steps the human must run after the agent finishes:

- [ ] `python manage.py makemigrations core`
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_test` (for test DB)
- [ ] `python manage.py migrate` (for production DB)
- [ ] Review and push feature branch: `git push origin feature/rbc-annual-report-import`
- [ ] Verify model naming: all models prefixed with `RbcAnnual` to distinguish from other broker reports (e.g., IB)
- [ ] Verify annual report import command: `python manage.py manage_annual_report_import --dry-run specs/`
- [ ] Run tests: `python manage.py test imports --settings=portfolio_manager.settings_test`

---

## Human Approval Required

Human approval required before any git push or DB migration.

---

## Appendix: RBC Annual Report PDF Structure Reference

### Sample files in specs folder

| File | Pages | Account Type | Account Number |
|------|-------|-------------|----------------|
| `Annual Report-2259 2025-12-31.pdf` | 6 | RIF | 900-00005 |
| `Annual Report-4033 2025-12-31.pdf` | 4 | LIF | 900-00008 |
| `Annual Report-5362 2025-12-31.pdf` | 6 | RRSP | 900-00003 |
| `Annual Report-7620 2025-12-31.pdf` | 6 | IF | 690-7762 |

### Page layout (6-page example)

**Pages 1–3: Annual Investment Performance Report**
- Page 1: Account info, CAD account change in value table
- Page 2: CAD amount invested summary, CAD money-weighted returns, USD account change in value
- Page 3: USD amount invested summary, USD money-weighted returns, additional notes

**Pages 4–6: Annual Charges and Compensation Report**
- Page 4: CAD charges (operating + transaction), CAD third-party compensation
- Page 5: USD charges, USD third-party compensation, additional notes
- Page 6: Additional notes continued, legal disclaimers, trademarks

### 4-page example (e.g., account 4033)

**Pages 1–2: Annual Investment Performance Report**
- Page 1: Account info, CAD account change in value
- Page 2: CAD amount invested, CAD money-weighted returns, additional notes

**Pages 3–4: Annual Charges and Compensation Report**
- Page 3: CAD charges, CAD third-party compensation
- Page 4: Additional notes, legal disclaimers

### Key data points to extract

**Balances section (per currency — CAD and USD):**
- Beginning market value
- Deposits and transfers-in
- Withdrawals and transfers-out
- Net investment return
- Ending market value
- Amount invested

**Performance section (per currency — CAD and USD):**
- Money-weighted returns: 1yr, 3yr, 5yr, 10yr, since-date percentages

**Charges section (per currency — CAD and USD):**
- Operating charges
- Transaction charges — Commissions Equities/options
- Total transaction charges
- Total paid by you

**Compensation section (per currency — CAD and USD):**
- Trailing commission
- Other compensation (if any)
- Total compensation from third parties
