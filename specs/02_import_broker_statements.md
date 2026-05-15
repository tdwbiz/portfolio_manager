**Feature Spec: Import IB and RBC Broker Statements**

Project name: portfolio_manager
Customer: tdwdev
Database: Microsoft SQL Server (MSSQL) — existing DB "PortfolioDev", connection via .env
Django version: 5.1 (existing project — do NOT recreate, update in place)
Feature branch: feature/import-broker-statements

Requirements:

1. IB (Interactive Brokers) CSV import
   - Parse IB Activity Statement CSV format
   - Detect and skip header/section rows (IB CSVs have multi-section format with labels like "Trades", "Dividends", etc.)
   - Import only the "Trades" section rows into the Transaction model
   - Map IB columns to Transaction fields:
     - Date/Time → transaction.date (parse to date, discard time)
     - Symbol → transaction.symbol
     - Buy/Sell → transaction.type
     - Quantity → transaction.quantity
     - T. Price → transaction.price
     - Comm/Fee → transaction.commission
     - Proceeds → transaction.amount
     - Currency → transaction.currency
   - Create a BrokerStatement record first, then link all transactions to it via FK

2. RBC (Royal Bank) CSV import
   - Parse RBC Direct Investing trade confirmation CSV format
   - Map RBC columns to Transaction fields (column names differ from IB)
   - Create a BrokerStatement record first, link all transactions to it via FK

3. Django management command: import_statements
   - Location: imports/management/commands/import_statements.py
   - Usage: python manage.py import_statements --broker IB --file path/to/file.csv
   - Usage: python manage.py import_statements --broker RBC --file path/to/file.csv
   - Options: --dry-run (parse and report, do not save to DB)
   - On success: print count of statements and transactions imported
   - On duplicate detection: skip rows already imported (check by broker + date + symbol + quantity)

4. Duplicate detection
   - Before inserting a Transaction, check for an existing row with the same:
     broker_statement.source, date, symbol, type, quantity, price
   - If duplicate: skip silently (log at DEBUG level)
   - Report total skipped count at end of import

5. Error handling
   - If a row fails parsing: log the error with the row number and content, continue processing remaining rows
   - If the file cannot be opened: raise CommandError with a clear message
   - Do not leave partial data on failure — use a database transaction wrapping the full import

6. Tests (imports/tests/test_import_statements.py)
   - Test IB import with a sample CSV fixture (5–10 rows)
   - Test RBC import with a sample CSV fixture
   - Test duplicate detection — run same file twice, second run imports 0 rows
   - Test --dry-run — nothing written to DB
   - Use Django TestCase with an in-memory or test DB

Constraints:
- Do NOT run python manage.py migrate — migrations are a manual step
- Do NOT push to git — output manual_steps_required
- Follow PEP8, add type hints to all functions
- Use Python's csv module — no pandas dependency
- All file paths are absolute or relative to where the command is run

Context:
Spec 01 (Bootstrap) created the Django project at C:\AI-DevTeam\projects\portfolio_manager.
The core/models.py contains BrokerStatement and Transaction models.
The imports app exists but has no logic yet.
This spec adds the actual import capability.
After this is done, the next step will be a basic web view showing imported transactions.

Human approval required before any git push or DB changes.
