# Feature Spec: Transaction Export (CSV and Excel)

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/transaction-export

---

## Context

Specs 01–07 are complete. The transaction list at `/transactions/` supports filters:
broker, symbol, type, account, date_from, date_to (Spec 05/1.3). Users want to
download the filtered results as CSV or Excel for use in spreadsheet tools.

The export must apply the same filter logic as the existing `transaction_list` view.
No new models or migrations needed.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- New dependency: `openpyxl>=3.1` (for Excel export)

---

## Requirements

1. **Add `openpyxl>=3.1` to `requirements.txt`**

2. **New helper `_apply_transaction_filters(qs, request)` in `core/views.py`**
   - Extracts the shared filter logic from `transaction_list` into a private helper.
   - Takes a queryset and request; returns the filtered queryset and filter values as a dict.
   - Refactor `transaction_list` to use it, so export views can reuse the same logic.
   - Signature:
     ```python
     def _apply_transaction_filters(qs, request):
         ...
         return qs, {'broker': broker, 'symbol': symbol, 'type': tx_type,
                     'account': account_id, 'date_from': date_from, 'date_to': date_to}
     ```

3. **New view `transaction_export_csv` in `core/views.py`**
   - URL: `/transactions/export/csv/`
   - Applies the same filters via `_apply_transaction_filters`.
   - Returns an `HttpResponse` with `Content-Type: text/csv` and
     `Content-Disposition: attachment; filename="transactions_<YYYY-MM-DD>.csv"`.
   - Columns (in order): Date, Symbol, Type, Quantity, Price, Amount, Currency,
     Amount (CAD), Exchange Rate, Account, Broker, Notes.
   - Use Python's `csv.writer`. No third-party library for CSV.

4. **New view `transaction_export_excel` in `core/views.py`**
   - URL: `/transactions/export/excel/`
   - Applies the same filters via `_apply_transaction_filters`.
   - Returns an `HttpResponse` with
     `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
     and `Content-Disposition: attachment; filename="transactions_<YYYY-MM-DD>.xlsx"`.
   - Uses `openpyxl` to create a workbook with one sheet named "Transactions".
   - Same columns as CSV export. Header row bold.
   - Write to a `BytesIO` buffer and return its content.

5. **Two new URLs in `core/urls.py`**
   - Add before `transactions/` or after it, but before `portfolio/`:
     ```python
     path('transactions/export/csv/', views.transaction_export_csv, name='transaction_export_csv'),
     path('transactions/export/excel/', views.transaction_export_excel, name='transaction_export_excel'),
     ```
   - **Important:** these paths must be defined BEFORE `transactions/` if Django would
     otherwise match them ambiguously — but since they are sub-paths, order relative to
     `transactions/` does not matter. Place them immediately after `transactions/`.

6. **Update `core/templates/core/transaction_list.html`**
   - Add download buttons below the filter form, passing current filter params:
     ```html
     <a href="{% url 'transaction_export_csv' %}?broker={{ broker_filter|default:'' }}&symbol={{ symbol_filter|default:'' }}&type={{ type_filter|default:'' }}&account={{ account_filter|default:'' }}&date_from={{ date_from }}&date_to={{ date_to }}">Download CSV</a>
     &nbsp;
     <a href="{% url 'transaction_export_excel' %}?broker={{ broker_filter|default:'' }}&symbol={{ symbol_filter|default:'' }}&type={{ type_filter|default:'' }}&account={{ account_filter|default:'' }}&date_from={{ date_from }}&date_to={{ date_to }}">Download Excel</a>
     ```

7. **Tests in `core/tests/test_export.py`**
   - `TestCase` class `TransactionExportTests`.
   - `setUp`: create Account, BrokerStatement, 3 transactions spanning two symbols
     (AAPL × 2, MSFT × 1). All with unique `tx_hash` and `commission=Decimal('0')`.
   - `test_csv_export_status`: GET `/transactions/export/csv/` returns 200.
   - `test_csv_content_type`: response has `Content-Type` starting with `text/csv`.
   - `test_csv_contains_header`: response content contains `b'Date'` and `b'Symbol'`.
   - `test_csv_contains_data`: response content contains `b'AAPL'`.
   - `test_csv_filter_by_symbol`: GET with `?symbol=MSFT` returns CSV containing
     `MSFT` but NOT `AAPL`.
   - `test_excel_export_status`: GET `/transactions/export/excel/` returns 200.
   - `test_excel_content_type`: response `Content-Type` contains `spreadsheetml`.

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations.
- Use Python stdlib `csv` for CSV export — no pandas.
- Use `openpyxl` for Excel — do not use xlwt or xlrd.
- The refactored `_apply_transaction_filters` must not break existing `transaction_list` behaviour.
- Run `python manage.py test core --settings=portfolio_manager.settings_test`. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: CSV and Excel export for transaction list"`

---

## Manual Steps Required

- [ ] `pip install openpyxl>=3.1` (or it will be installed via requirements.txt)
- [ ] Review and push feature branch: `git push origin feature/transaction-export`
- [ ] Merge to dev after review.
