# Views API Reference

## Core Views (`core/views_*.py`)

### Authentication (`core/views_auth.py`)

#### `login_view(request)`
Handle user login with rate limiting.

**POST**: Authenticates user, records login attempt.
**GET**: Shows login form.

### Portfolio (`core/views_portfolio.py`)

#### `dashboard_view(request)`
Show portfolio dashboard with summary metrics.

**GET**: Returns dashboard with portfolio value, returns, positions.

#### `realised_pnl_view(request)`
Show realised P&L by account/symbol.

**GET**: Returns P&L summary with filters.

### Transactions (`core/views_transactions.py`)

#### `transaction_list_view(request)`
List and filter transactions.

**GET**: Returns transaction list with filters (account, date range, symbol).

### Accounts (`core/views_accounts.py`)

#### `account_list_view(request)`
List all accounts.

#### `account_create_view(request)`
Create a new account.

#### `account_update_view(request, account_id)`
Update an existing account.

#### `account_delete_view(request, account_id)`
Delete an account.

### Reports (`core/views_reports.py`)

#### `tax_summary_view(request)`
Show tax summary (realised gains/losses).

#### `income_summary_view(request)`
Show income summary (dividends, interest).

#### `export_view(request)`
Export data to CSV.

## Import Views (`imports/views.py`)

### `pdf_import_view(request)`
Handle PDF statement upload and parsing.

**GET**: Shows upload form + existing statements.
**POST**: Validates PDF, parses file, creates records.

### `annual_report_import_view(request)`
Handle RBC Annual Report upload via VL model.

**GET**: Shows upload form + existing annual reports.
**POST**: Parses PDF via VL model, creates flat RbcAnnual rows.

## Risk Views (`risk/views/*.py`)

### Dashboard (`risk/views/dashboard.py`)

#### `risk_dashboard_view(request)`
Show risk analytics dashboard.

### Security Metrics (`risk/views/security.py`)

#### `security_detail_view(request, symbol)`
Show detailed risk metrics for a security.

### Strategy (`risk/views/strategy.py`)

#### `strategy_report_view(request, rec_id)`
Show strategy recommendation report.

### Backtesting (`risk/views/backtest.py`)

#### `backtest_list_view(request)`
List all backtest results.

#### `backtest_detail_view(request, backtest_id)`
Show detailed backtest results.
