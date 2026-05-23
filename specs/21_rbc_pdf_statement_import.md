# Feature Spec: RBC PDF Statement Import

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/rbc-pdf-import

---

## Context

The application currently imports RBC statements via CSV (parser in `imports/parsers.py::parse_rbc_csv`). The CSV format is a structured export from RBC's online portal with columns: Date, Activity, Symbol, Description, Quantity, Price, Value, Account ID, Currency, Notes.

RBC also provides **PDF statements** (e.g. `Statement-5362 2025-09-30.pdf` in the specs folder). These are higher-quality, multi-page documents that contain richer data than the CSV:

- **Asset Summary** — market values by asset class (Cash, Mutual Funds, Foreign Securities, etc.) with percentages
- **Asset Review** — detailed security positions with quantity, market price, book cost, market value
- **Account Activity** — transactions with date, activity type, description, price/rate, quantity, debit/credit
- **Cash Balance** — opening and closing cash balances per account type
- **Income Summary** — dividends, interest, total income (this month and YTD)
- **Contribution Summary** — personal contribution tracking (first 60 days / balance of year)
- **FX rate** — exchange rate as of statement date (e.g. "1 USD = 1.39185 CAD as of SEPT 30, 2025")
- **Account metadata** — account number, account type (RRSP), statement date, last statement date

The PDF is significantly more reliable than CSV because:
1. It's the official statement — less missing data
2. It includes book cost per security (not available in CSV)
3. It includes opening/closing balances (not in CSV)
4. It includes contribution tracking (not in CSV)
5. It includes FX rate at statement date (not in CSV)

This spec adds a **separate PDF import pipeline** that creates new models (`PdfTransaction`, `PdfAccountBalance`) alongside the existing `Transaction` and `BrokerStatement` models. PDF data does NOT replace CSV data — both coexist. The UI will show them separately or merged on demand.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- PDF parsing: PyMuPDF (`fitz`) — already installed in the environment
- Frontend: Bootstrap 5.3.2 (CDN)

---

## Requirements

### 1. Install PDF parsing dependency

Add `PyMuPDF>=1.24.0` (package name: `PyMuPDF`, imports as `fitz`) to `requirements.txt`.

```
PyMuPDF>=1.24.0
```

This library extracts text from PDFs reliably on Windows. It is faster and more accurate than `pdfplumber` or `pypdf` for RBC's fixed-layout statements.

### 2. Create new Django models

Add the following models to `core/models.py` (after `Transaction`, before `FileImportLog`):

```python
class PdfStatement(models.Model):
    """A parsed RBC PDF statement — one per statement file."""
    account = models.ForeignKey(
        'Account',
        on_delete=models.CASCADE,
        related_name='pdf_statements',
    )
    file_name = models.CharField(max_length=255)
    statement_date = models.DateField()
    last_statement_date = models.DateField(null=True, blank=True, help_text='Date of previous statement')
    account_number = models.CharField(max_length=50, blank=True, default='', help_text='RBC account number from PDF')
    fx_rate = models.DecimalField(
        max_digits=10, decimal_places=6, default=Decimal('1.000000'),
        help_text='USD to CAD exchange rate as of statement date',
    )
    parsed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-statement_date']

    def __str__(self):
        return f"PDF Statement {self.account} — {self.statement_date}"


class PdfAccountBalance(models.Model):
    """Snapshot of account balances at statement date — from the Asset Summary section."""
    pdf_statement = models.ForeignKey(
        PdfStatement,
        on_delete=models.CASCADE,
        related_name='balances',
    )
    account_type = models.CharField(
        max_length=50,
        help_text='e.g. "Cash", "Mutual Funds", "Foreign Securities", "Preferred Shares", "Common Shares", "Fixed Income", "Other"',
    )
    opening_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Opening balance at last statement date',
    )
    closing_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Closing balance at this statement date',
    )
    market_value = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        help_text='Market value at this statement date',
    )
    percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        help_text='Percentage of total portfolio',
    )

    class Meta:
        ordering = ['account_type']
        unique_together = [('pdf_statement', 'account_type')]

    def __str__(self):
        return f"{self.pdf_statement} — {self.account_type}: ${self.market_value}"


class PdfTransaction(models.Model):
    """Individual transaction from the Account Activity section of a PDF statement."""
    pdf_statement = models.ForeignKey(
        PdfStatement,
        on_delete=models.CASCADE,
        related_name='transactions',
    )
    date = models.DateField()
    activity_type = models.CharField(max_length=50, help_text='e.g. "INTEREST", "BUY", "SELL", "DIVIDEND"')
    description = models.TextField(blank=True, default='', help_text='Full activity description from PDF')
    symbol = models.CharField(max_length=50, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    price = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), help_text='Amount debited (outflow)')
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), help_text='Amount credited (inflow)')
    currency = models.CharField(max_length=3, default='CAD')
    commission = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.00'))
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['date', 'id']
        indexes = [
            models.Index(fields=['pdf_statement', 'date']),
        ]

    def __str__(self):
        return f"{self.activity_type} {self.date} — {self.description[:50]}"
```

**Model design notes:**
- `PdfStatement` is the parent record — one per PDF file, linked to an `Account`.
- `PdfAccountBalance` captures the Asset Summary breakdown (Cash, Mutual Funds, etc.) and the Cash Balance table (opening/closing).
- `PdfTransaction` captures each row from the Account Activity section.
- PDF models are **separate** from `Transaction` — they do NOT replace CSV data.
- `tx_hash` is NOT used on `PdfTransaction` — deduplication works at the `PdfStatement` level (by filename + statement_date).
- `Fx_rate` is stored on `PdfStatement` — it's the rate as of the statement date, useful for point-in-time conversions.

### 3. Create PDF parser: `imports/parsers_pdf.py`

Create a new file `imports/parsers_pdf.py` with a single public function:

```python
def parse_rbc_pdf(file_path: str) -> dict:
    """Parse an RBC PDF statement and return structured data.

    Returns:
        {
            'statement_date': date,
            'last_statement_date': date | None,
            'account_number': str,
            'fx_rate': Decimal,
            'balances': [
                {'account_type': str, 'opening': Decimal, 'closing': Decimal,
                 'market_value': Decimal, 'percentage': Decimal},
                ...
            ],
            'transactions': [
                {'date': date, 'activity_type': str, 'description': str,
                 'symbol': str, 'quantity': Decimal, 'price': Decimal,
                 'debit': Decimal, 'credit': Decimal, 'currency': str,
                 'commission': Decimal},
                ...
            ],
            'income_summary': {
                'dividends_this_month': Decimal, 'dividends_ytd': Decimal,
                'interest_this_month': Decimal, 'interest_ytd': Decimal,
                'total_this_month': Decimal, 'total_ytd': Decimal,
            },
            'contribution_summary': {
                'first_60_days': Decimal, 'balance_of_year': Decimal,
            },
        }
    """
```

**Parsing strategy — text extraction + regex:**

Use PyMuPDF (`fitz`) to extract text page by page. RBC PDFs have a fixed layout with clear section headers. Parse each page by looking for section markers:

**Page 1 — Asset Summary, Income Summary, Cash Balance, Contribution Summary:**

```python
# Key regex patterns for page 1
ASSET_SUMMARY_HEADER = re.compile(r'Asset\s+Summary', re.IGNORECASE)
CASH_BALANCE_HEADER = re.compile(r'Cash\s+Balance', re.IGNORECASE)
INCOME_SUMMARY_HEADER = re.compile(r'Income\s+Summary', re.IGNORECASE)
CONTRIBUTION_HEADER = re.compile(r'Contribution\s+Summary', re.IGNORECASE)
```

**Asset Summary parsing:**
The Asset Summary table has two rows of headers (asset class names, then "MARKET VALUE / AT [DATE]" and "%"), followed by data rows. Each row has:
- Asset class name (e.g. "Cash", "Mutual Funds", "Foreign Securities")
- Market value (e.g. "$20.85", "$1,036.34")
- Percentage (e.g. "1.97 %", "98.03 %")

Parse by finding the "Asset Summary" header, then reading the next block of lines. The market value and percentage columns are the last two numeric values on each data line.

**Cash Balance parsing:**
The Cash Balance table has columns: ACCOUNT TYPE, OPENING BALANCE AT [DATE], CLOSING BALANCE AT [DATE]. Parse by finding the "Cash Balance" header, then reading the table rows.

**Income Summary parsing:**
The Income Summary table has columns: DIVIDENDS, INTEREST, TOTAL INCOME — each with THIS MONTH and YEAR-TO-DATE sub-columns. Parse by finding the "Income Summary" header.

**Contribution Summary parsing:**
The Contribution Summary table has columns: PERSONAL CONTRIBUTION — First 60 days, Balance of Year. Parse by finding the "Contribution Summary" header.

**Page 2 — Asset Review, Account Activity:**

```python
ASSET_REVIEW_HEADER = re.compile(r'Asset\s+Review', re.IGNORECASE)
ACCOUNT_ACTIVITY_HEADER = re.compile(r'Account\s+Activity', re.IGNORECASE)
```

**Asset Review parsing:**
The Asset Review table has columns: SYMBOL, SECURITY, QUANTITY, MKT. PRICE, BOOK COST, MARKET VALUE. Parse by finding the "Asset Review" header. Each security may span 2 lines (name on line 1, symbol + details on line 2).

**Account Activity parsing:**
The Account Activity table has columns: DATE, ACTIVITY, DESCRIPTION, PRICE/RATE, QUANTITY, DEBIT, CREDIT. Parse by finding the "Account Activity" header. Each transaction row has:
- Date (e.g. "SEPT 02")
- Activity type (e.g. "INTEREST")
- Description (multi-line, e.g. "RBC INVESTMENT SAVINGS ACCOUNT SR A (2010) AS OF 08/29/25 REINVEST @ $10.00")
- Price/Rate (e.g. "0.209")
- Quantity (may be blank for non-trade activities)
- Debit (may be blank)
- Credit (may be blank)

**Page 3 — Footnotes (ignored):**
Page 3 typically contains footnotes and disclaimers. No data to extract.

**FX rate extraction:**
The FX rate appears in the Asset Review header line:
```
( Exchange rate 1USD = 1.39185 CAD as of SEPT 30, 2025 )
```
Parse with: `re.search(r'Exchange rate\s+1\s*USD\s*=\s*([\d.]+)\s*CAD', text)`

**Account metadata extraction:**
- Account number: `re.search(r'Your Account Number:\s*(\S+-\S+-\S+-\S+)', text)`
- Statement date: from the header "SEPT 30 2025"
- Last statement date: `re.search(r'Date of Last Statement:\s*(.+)', text)`

**Date parsing helper:**
RBC uses abbreviated month names: "SEPT 30", "AUG. 29". Support both formats:
```python
def _parse_rbc_date(month_str: str, year: int) -> date:
    """Parse RBC-style date like 'SEPT 30' or 'AUG. 29' with known year."""
    month_str = re.sub(r'\.', '', month_str).upper()
    month_map = {
        'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
        'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    }
    parts = month_str.split()
    month = month_map.get(parts[0][:3])
    day = int(parts[1]) if len(parts) > 1 else 1
    return date(year, month, day)
```

**Error handling:**
- Replace bare `except Exception:` with `except Exception as e:` and `logger.warning()`.
- If a section is missing (e.g. no Income Summary), return empty dict for that section — do NOT raise.
- If the PDF cannot be parsed at all, raise `ValueError("Unable to parse RBC PDF: <reason>")`.

### 4. Create management command: `manage_pdf_import`

Create `core/management/commands/manage_pdf_import.py` (or add to an existing command file). This command:

1. Accepts a directory path or file path as argument
2. For each `.pdf` file found:
   a. Parse with `parse_rbc_pdf()`
   b. Look up or create the `Account` (match by account number from PDF, or prompt)
   c. Check for duplicate `PdfStatement` (same account + statement_date) — skip if exists
   d. Create `PdfStatement` record
   e. Create `PdfAccountBalance` records for each asset class
   f. Create `PdfTransaction` records for each activity row
   g. Log summary: files processed, statements created, transactions imported, balances recorded

```python
# Command usage
python manage.py manage_pdf_import /path/to/pdf/statement/folder
python manage.py manage_pdf_import /path/to/single/file.pdf
python manage.py manage_pdf_import --dry-run /path/to/folder
```

**Dry-run mode:** Parse and display what would be imported without writing to DB.

**Duplicate handling:** If a `PdfStatement` with the same account + statement_date already exists, skip the file and log a warning. Do NOT overwrite existing data.

### 5. Create import view and template

Add a new view and template for PDF statement imports:

**View: `imports/views.py` — `pdf_import_view`**
- GET: Show upload form + list of existing PDF statements
- POST: Handle file upload, parse PDF, create records, show results

**Template: `imports/templates/core/pdf_import.html`**
- File upload area (drag-and-drop or file picker)
- Results table showing parsed data:
  - Statement date, account number, FX rate
  - Balances by asset class (table)
  - Transactions (table, same format as existing transaction list but with PDF-specific columns)
- Link to merge PDF transactions into the main `Transaction` table (future enhancement)

### 6. Create tests

Create `imports/tests/test_parsers_pdf.py`:

- **Test 1: Parse sample PDF** — Use `Statement-5362 2025-09-30.pdf` as fixture. Verify:
  - `statement_date` == date(2025, 9, 30)
  - `last_statement_date` == date(2025, 8, 29)
  - `account_number` == "900-00003-1-8"
  - `fx_rate` == Decimal("1.39185")
  - `balances` includes: Cash (market_value=$20.85, 1.97%), Mutual Funds (market_value=$1,036.34, 98.03%)
  - `transactions` includes: INTEREST on SEPT 02, RBC INVESTMENT SAVINGS, credit=$0.209
  - `income_summary` shows $0.00 for all fields (no income this period)
  - `contribution_summary` shows $0.00 for both fields

- **Test 2: Parse multi-security PDF** — Create a synthetic PDF text that mimics a statement with multiple securities, buys, sells, and dividends. Verify all sections parse correctly.

- **Test 3: Missing sections** — Parse a PDF text that omits Income Summary and Contribution Summary. Verify those sections return empty dicts without raising.

- **Test 4: Invalid PDF** — Pass a non-PDF file. Verify `ValueError` is raised.

- **Test 5: Management command dry-run** — Run the command with `--dry-run` and verify no DB writes occur.

- **Test 6: Duplicate detection** — Import the same PDF twice. Verify the second import is skipped with a warning.

Use `--settings=portfolio_manager.settings_test` for all tests.

---

## Constraints

Hard rules the agent must not violate:

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- PDF models are **separate** from existing `Transaction` / `BrokerStatement` — no changes to those models
- Do NOT modify existing parsers (`imports/parsers.py`) — only add new file `imports/parsers_pdf.py`
- Do NOT merge PDF transactions into the main `Transaction` table — that is a future enhancement
- Use `Decimal` for all monetary values
- Date parsing must handle RBC's abbreviated month format (e.g. "SEPT 30", "AUG. 29")
- Add `PyMuPDF>=1.24.0` to `requirements.txt`
- Replace any bare `except Exception:` in the new parser with `except Exception as e:` + `logger.warning()`
- The parser must handle PDFs with 2 pages (minimal) and 3+ pages (with footnotes)

---

## Manual Steps Required

Steps the human must run after the agent finishes:

- [ ] `& "C:\AI-DevTeam\ai-env\Scripts\python.exe" "C:\AI-DevTeam\projects\portfolio_manager\manage.py" makemigrations`
- [ ] `& "C:\AI-DevTeam\ai-env\Scripts\python.exe" "C:\AI-DevTeam\projects\portfolio_manager\manage.py" migrate --settings=portfolio_manager.settings`
- [ ] Review and push feature branch: `git push origin feature/rbc-pdf-import`
- [ ] Copy RBC PDF statement files into the project for testing
- [ ] Run the management command: `python manage.py manage_pdf_import /path/to/pdfs`

---

## Human Approval Required

Human approval required before any git push or DB changes.

---

## Appendix: RBC PDF Layout Reference

### Page 1 — Summary Sections

```
[Header: Account holder name, address, RRSP Statement (CDN$), SEPT 30 2025, Account Number, Last Statement Date]

─────────────────────────────────────────────────────────────
Asset Summary
─────────────────────────────────────────────────────────────
[Table: Asset Class | Market Value at SEPT 30 | %]
  Cash                    $20.85          1.97%
  Mutual Funds          $1,036.34        98.03%
  Foreign Securities      $0.00          0.00%
  ...
  Total                 $1,057.19       100.00%

─────────────────────────────────────────────────────────────
Income Summary
─────────────────────────────────────────────────────────────
[Table: | This Month | Year-to-Date]
  Dividends               $0.00        $0.00
  Interest                $0.00        $0.00
  Total Income            $0.00        $0.00

─────────────────────────────────────────────────────────────
Cash Balance
─────────────────────────────────────────────────────────────
[Table: Account Type | Opening Balance at AUG. 29 | Closing Balance at SEPT 30]
  Cash                    $20.85         $20.85
  Fixed Income            $0.00          $0.00

─────────────────────────────────────────────────────────────
Contribution Summary
─────────────────────────────────────────────────────────────
[Table: Personal Contribution | First 60 days | Balance of Year]
                            $0.00          $0.00
```

### Page 2 — Positions & Activity

```
─────────────────────────────────────────────────────────────
Asset Review  (Exchange rate 1USD = 1.39185 CAD as of SEPT 30, 2025)
─────────────────────────────────────────────────────────────
[Table: Symbol | Security | Quantity | Mkt Price | Book Cost | Market Value]
  RBF2010  RBC INVESTMENT SAVINGS  103.634  10.000  1,036.34  $1,036.34

─────────────────────────────────────────────────────────────
Account Activity
─────────────────────────────────────────────────────────────
[Table: Date | Activity | Description | Price/Rate | Quantity | Debit | Credit]
  SEPT 02  INTEREST  RBC INVESTMENT SAVINGS...  0.209           $0.209
```

### Page 3 — Footnotes (ignored)

```
FOOTNOTES, disclaimers, regulatory info, trademarks
```
