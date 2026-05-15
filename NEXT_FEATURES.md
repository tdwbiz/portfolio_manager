# Portfolio Manager — Next Features

**Date:** May 14, 2026  
**Purpose:** Prioritised list of enhancement ideas, from highest to lowest value.

Features are grouped by theme. Each item includes a short description of what it would do and why it matters.

---

## Theme 1: Account Organisation

### 1.1 Account Categories
Add a `category` field to the `Account` model with values such as **Retirement** (RRSP, LIRA), **Tax-Free** (TFSA), **Taxable** (margin, joint, cash), and **Other**. This single field unlocks grouped reporting across the entire app — portfolio summaries, P&L, and income can all be broken down by tax treatment, which is how most investors actually think about their money.

### 1.2 Account Owners / Beneficiaries
Add an optional `owner` field (e.g. "Tom", "Wife", "Joint") to distinguish whose account is whose in a family setup. Would allow filtering all reports by person without needing separate logins.

---

## Theme 2: Portfolio Summary Enhancements

### 2.1 Portfolio Summary by Account
Below the current all-accounts aggregate table, add collapsible sub-sections that group positions by account. Each account section would have its own subtotals for cost, market value, and unrealised P&L, so you can instantly see how each account is performing in isolation.

### 2.2 Portfolio Summary by Category
Alongside the by-account grouping, add a summary that groups positions by account category (Retirement, Tax-Free, Taxable). This gives a clear picture of total exposure in each tax bucket, which is essential for tax-efficient planning and withdrawal strategies.

### 2.3 Allocation % Column
Add a **% of Portfolio** column to the positions table showing each holding's weight in the total portfolio. Optionally, add a second column showing weight within its account or category. This makes concentration risk immediately visible.

### 2.4 Currency Breakdown Panel
Add a small summary panel showing total cost and market value split by currency (CAD holdings vs USD holdings). Since the app converts everything to CAD, it is easy to lose sight of how much currency risk you are carrying.

### 2.5 Sector / Asset Class Tags
Allow each symbol to be tagged with a sector (Technology, Financials, Energy, etc.) or asset class (Equities, Fixed Income, ETF, Cash). These tags could be maintained via admin or auto-populated from yfinance metadata. The portfolio summary could then show an allocation-by-sector breakdown.

---

## Theme 3: Performance Charts

### 3.1 Per-Account and Per-Category Performance Lines
Extend the portfolio chart to show separate lines for each account or each category, so you can visually compare how your RRSP is tracking against your TFSA over time. Would use the same monthly series logic already in place, filtered by account.

### 3.2 Cost Basis Overlay
Add a second line on the existing chart showing total invested cost over time. The gap between the market value line and the cost line is the unrealised P&L at a glance, without needing to read a table.

### 3.3 Benchmark Comparison
Add an optional overlay line showing a benchmark index (e.g. S&P 500 via `^GSPC`, TSX via `^GSPTSE`, or a simple 60/40 blend). Already feasible since yfinance is already in use. Would answer the most common question: "Am I beating the market?"

### 3.4 Chart Type and Frequency Options
Let the user choose between a line chart, area chart, and bar chart, and toggle between daily, weekly, and monthly data frequency. Currently the chart is fixed to a single monthly line chart with no controls on the page.

### 3.5 Date Range Selector
Add a date range picker to the chart page (e.g. 1M, 3M, 6M, YTD, 1Y, All). Currently the chart always shows the full history with no way to zoom in on a period of interest.

### 3.6 Dividend Income Overlay
Add an optional bar chart layer on the performance chart showing monthly or quarterly dividend and interest income received. This turns the chart from a pure price-based view into a total-return view.

---

## Theme 4: Reporting Improvements

### 4.1 Realised P&L — Date Range and Symbol Filters
Add date-from / date-to and symbol filters to the Realised P&L page. Currently you can only filter by account. For tax purposes, you need to see all trades closed within a specific tax year across all accounts.

### 4.2 Realised P&L — Grouped by Year
Add a year grouping toggle on the Realised P&L page so all trades in a given calendar year are grouped together with a year-end subtotal. This directly supports annual tax filing.

### 4.3 Income Summary — Account Filter
Add an account filter dropdown to the Income Summary page, consistent with the filter already on the Realised P&L page. Currently all income is shown in aggregate with no way to see which account generated it.

### 4.4 Annual Tax Summary Report
A dedicated page that consolidates all tax-relevant numbers for a selected year: realised gains/losses (FIFO and avg cost), dividends received by type (eligible vs non-eligible vs foreign), foreign tax withheld, and total income. Output should be printable or exportable to PDF.

### 4.5 Total Return Calculation
Calculate total return (unrealised P&L + realised P&L + income received) for each position, account, and the whole portfolio. This is the number that actually matters for measuring investment performance.

---

## Theme 5: Dashboard Enhancements

### 5.1 Portfolio Dollar Summary on Dashboard
Add a top-level summary row to the dashboard showing total portfolio market value, total unrealised P&L, and total income received year-to-date. Currently the dashboard only shows counts (accounts, transactions, brokers) with no dollar values.

### 5.2 Top Movers Widget
Add a small table showing the best and worst performing positions today or over a configurable period (e.g. last 30 days). Uses data already fetched from yfinance.

### 5.3 Upcoming Dividends / Income Tracker
If dividend schedule data is available (e.g. from yfinance or a simple manual table), show a widget listing expected dividend payments in the next 30–90 days. Useful for cash flow planning.

---

## Theme 6: Import System

### 6.1 Additional Broker Support
Add parsers for other Canadian or US brokers (e.g. Questrade, TD Direct Investing, Wealthsimple, Fidelity). The parser architecture is already pluggable — adding a new broker is a self-contained task.

### 6.2 Web-Based Import Upload
Add a file upload form in the UI so new statements can be imported through the browser without needing access to the server's inbox directory. Would display a live preview of parsed rows before committing.

### 6.3 Import Preview / Dry Run UI
Before committing an import, show a table of what would be imported, what would be skipped (duplicates), and what was rejected. Currently dry-run output is only available via management command on the server.

### 6.4 Import Notifications
Send an in-app notification (or email) when an import completes or fails, showing a summary of records imported and any errors. Currently you need to check the Import History page manually.

---

## Theme 7: Data Quality

### 7.1 FX Rate Management UI
Add a simple admin UI page showing all transactions where `exchange_rate_cad = 1.0` on a USD transaction (which means the CAD conversion is wrong). Currently this requires running the `audit_fx` management command on the server. Would allow manual correction of rates through the browser.

### 7.2 Bulk Transaction Edit
Allow selecting multiple transactions and applying a batch update (e.g. assign account, fix exchange rate, change type). Currently each transaction can only be edited one at a time through Django Admin.

### 7.3 Symbol Alias / Mapping
Allow mapping ticker symbols that have changed over time (e.g. a stock that was renamed or relisted) so that historical positions are correctly tracked as a single continuous holding.

---

## Priority Summary

| Priority | Feature |
|---|---|
| 🔴 High | 1.1 Account Categories |
| 🔴 High | 2.1 Portfolio Summary by Account |
| 🔴 High | 2.2 Portfolio Summary by Category |
| 🔴 High | 4.1 Realised P&L — Date Range and Symbol Filters |
| 🔴 High | 4.2 Realised P&L — Grouped by Year |
| 🟡 Medium | 2.3 Allocation % Column |
| 🟡 Medium | 3.1 Per-Account / Per-Category Performance Lines |
| 🟡 Medium | 3.2 Cost Basis Overlay |
| 🟡 Medium | 3.5 Date Range Selector on Chart |
| 🟡 Medium | 4.3 Income Summary — Account Filter |
| 🟡 Medium | 4.4 Annual Tax Summary Report |
| 🟡 Medium | 5.1 Portfolio Dollar Summary on Dashboard |
| 🟢 Lower | 3.3 Benchmark Comparison |
| 🟢 Lower | 3.4 Chart Type and Frequency Options |
| 🟢 Lower | 6.2 Web-Based Import Upload |
| 🟢 Lower | 7.1 FX Rate Management UI |
| 🟢 Lower | 1.2 Account Owners / Beneficiaries |
| 🟢 Lower | 4.5 Total Return Calculation |
| 🟢 Lower | 2.5 Sector / Asset Class Tags |
| 🟢 Lower | 6.1 Additional Broker Support |
