# Feature Spec: Dividend and Income Summary

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/income-summary

---

## Context

Specs 01–07 are complete. Transactions use canonical type codes (Spec 05):
DIV (dividend), DIST (distribution), INT (interest), WITHTAX (withholding tax),
TAX (tax). Income transactions have positive `amount_cad` values.

This spec adds an income summary page grouped by symbol and calendar year — useful
for tax reporting. No new models or migrations needed.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

1. **New view `income_summary` in `core/views.py`**
   - URL: `/income/`
   - Income transaction types to include: `DIV`, `DIST`, `INT` (positive income).
   - Tax/withholding types to include as negatives: `WITHTAX`, `TAX`.
   - Accepts GET params:
     - `year` (optional int): filter to a specific calendar year.
     - `type` (optional str): filter to a specific type code.
   - Queries `Transaction` objects filtered to the income + tax types (and any additional year/type filters).
   - Groups results in Python (not the DB) by `(year, symbol, type)`. For each group compute:
     - `total_amount_cad`: sum of `amount_cad`
     - `count`: number of transactions
   - Builds a list of summary rows sorted by `year` desc, then `symbol` asc, then `type` asc.
   - Computes grand total `total_income_cad` = sum of `total_amount_cad` for income types (DIV, DIST, INT only).
   - Computes `total_tax_cad` = sum of `total_amount_cad` for tax types (WITHTAX, TAX only — will be negative).
   - Computes `net_income_cad` = `total_income_cad + total_tax_cad`.
   - Passes to template: `rows`, `total_income_cad`, `total_tax_cad`, `net_income_cad`,
     `years` (distinct sorted years from results, for the filter dropdown),
     `income_types` (the list of relevant type codes),
     `year_filter`, `type_filter`.

2. **New URL in `core/urls.py`**
   - Add after `realised-pnl/`:
     ```python
     path('income/', views.income_summary, name='income_summary'),
     ```

3. **Add nav link in `core/templates/core/base.html`**
   - Add after the "Realised P&L" link (or after "Portfolio" if Spec 08 is not yet merged):
     ```html
     <a href="{% url 'income_summary' %}">Income</a>
     ```

4. **New template `core/templates/core/income_summary.html`**
   - Extends `core/base.html`.
   - Header: `<h1>Dividend & Income Summary</h1>`
   - Summary bar:
     ```html
     <p>
       Gross Income (CAD): <strong>{{ total_income_cad|floatformat:2 }}</strong> &nbsp;|&nbsp;
       Tax Withheld (CAD): <strong style="color:red">{{ total_tax_cad|floatformat:2 }}</strong> &nbsp;|&nbsp;
       Net Income (CAD): <strong>{{ net_income_cad|floatformat:2 }}</strong>
     </p>
     ```
   - Filter form (GET method):
     - Year dropdown: `<option value="">All Years</option>` + `{% for y in years %}...{% endfor %}`.
     - Type dropdown: `<option value="">All Types</option>` + one option per type code.
     - Submit button.
   - Results table columns: Year | Symbol | Type | Count | Total (CAD)
   - Use `|floatformat:2` on all Decimal columns.
   - If `rows` is empty, show `<p>No income transactions found.</p>`.

5. **Tests in `core/tests/test_income_summary.py`**
   - `TestCase` class `IncomeSummaryTests`.
   - `setUp`: create Account, BrokerStatement, and transactions:
     - DIV AAPL 2024: amount_cad=150.00, tx_hash='inc_div1'
     - DIV AAPL 2024: amount_cad=150.00, tx_hash='inc_div2'
     - INT cash 2024: amount_cad=50.00, tx_hash='inc_int1'
     - WITHTAX AAPL 2024: amount_cad=-45.00, tx_hash='inc_tax1'
     - DIV AAPL 2023: amount_cad=120.00, tx_hash='inc_div3'
     - BUY AAPL 2024: amount_cad=-2000.00, tx_hash='inc_buy1' (should NOT appear)
   - All Transaction creates include `commission=Decimal('0')`.
   - `test_income_page_loads`: GET `/income/` returns 200.
   - `test_income_shows_div`: response contains `b'DIV'`.
   - `test_income_excludes_buy`: response does NOT contain `b'inc_buy1'` or `b'-2000'`.
   - `test_income_year_filter`: GET with `?year=2024`, assert 2023 DIV row not shown.
   - `test_income_type_filter`: GET with `?type=INT`, assert response contains `b'INT'` but not `b'DIV'`.
   - `test_income_totals`: GET `/income/` with no filter, assert response contains `b'300.00'` (two DIV AAPL 2024 totalling 300).

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations.
- Group by year/symbol/type in Python — do not use database `GROUP BY` / `annotate`.
- Income types (DIV, DIST, INT) and tax types (WITHTAX, TAX) are hardcoded in the view as lists.
- Run `python manage.py test core --settings=portfolio_manager.settings_test`. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: dividend and income summary page"`

---

## Manual Steps Required

- [ ] Review and push feature branch: `git push origin feature/income-summary`
- [ ] Merge to dev after review.
