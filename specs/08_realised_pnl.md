# Feature Spec: Realised P&L (FIFO and Average Cost)

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/realised-pnl

---

## Context

Specs 01–07 are complete. The project has a working portfolio summary page (Spec 07)
showing open positions with live unrealised P&L. However, when a position is closed
(BUY followed by matching SELLs), the realised gain or loss is not tracked anywhere.

`core/utils.py` already contains `compute_positions()` which replays BUY/SELL
transactions. This spec adds a parallel `compute_realised_pnl()` function and a
dedicated page to display the results using both FIFO and average-cost methods
side by side.

Relevant model fields on `Transaction`: `symbol`, `type` (BUY/SELL), `quantity`,
`price`, `amount_cad`, `date`, `account`, `currency`, `exchange_rate_cad`.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

1. **New function `compute_realised_pnl(account_id=None)` in `core/utils.py`**
   - Optional `account_id` string to filter to a single account.
   - Fetch all BUY and SELL transactions, ordered by `date` ascending, then `id` ascending (stable sort).
   - Group by `(symbol, account_id)` pair.
   - For each group, run two independent lot-matching passes:

   **FIFO pass:**
   - Maintain a deque of open buy lots: each lot is `{'qty': Decimal, 'cost_cad': Decimal}` (cost_cad = `abs(amount_cad)` for the lot proportional to qty).
   - For each SELL: pop from the front, splitting the oldest lot if needed.
   - Each matched lot produces a closed trade dict (see below).

   **Avg-cost pass:**
   - Maintain running `total_shares` and `total_cost_cad` per group.
   - On BUY: `total_cost_cad += abs(amount_cad)`, `total_shares += quantity`.
   - On SELL: `avg_cost = total_cost_cad / total_shares`; realised_pnl_avg = `sell_amount_cad - (qty_sold * avg_cost)`; reduce `total_cost_cad` and `total_shares` proportionally.

   - Each closed trade dict contains:
     ```
     symbol, account_id, sell_date, qty_sold,
     sell_amount_cad,          # abs(SELL transaction amount_cad)
     cost_basis_fifo,          # cost of matched lots (FIFO)
     cost_basis_avg,           # cost of matched lots (avg-cost)
     realised_pnl_fifo,        # sell_amount_cad - cost_basis_fifo
     realised_pnl_avg,         # sell_amount_cad - cost_basis_avg
     ```
   - Return a list of all closed trade dicts, sorted by `sell_date` descending.

2. **New view `realised_pnl` in `core/views.py`**
   - URL: `/realised-pnl/`
   - Accepts optional GET param `account` to filter by account_id.
   - Calls `compute_realised_pnl(account_id=account)`.
   - Computes summary totals: `total_pnl_fifo`, `total_pnl_avg` (sum over all rows).
   - Passes `trades`, `total_pnl_fifo`, `total_pnl_avg`, `accounts`, `account_filter` to template.
   - `accounts = Account.objects.all().order_by('account_id')` for the filter dropdown.

3. **New URL `realised-pnl/` in `core/urls.py`**
   - Add before the `accounts/` paths:
     ```python
     path('realised-pnl/', views.realised_pnl, name='realised_pnl'),
     ```

4. **New nav link in `core/templates/core/base.html`**
   - Add after the "Portfolio" link:
     ```html
     <a href="{% url 'realised_pnl' %}">Realised P&L</a>
     ```

5. **New template `core/templates/core/realised_pnl.html`**
   - Extends `core/base.html`.
   - Header: `<h1>Realised P&L</h1>`
   - Summary bar:
     ```html
     <p>
       Total Realised P&L (FIFO): <strong style="color:…">{{ total_pnl_fifo|floatformat:2 }}</strong>
       &nbsp;|&nbsp;
       Total Realised P&L (Avg Cost): <strong style="color:…">{{ total_pnl_avg|floatformat:2 }}</strong>
     </p>
     ```
     Use `style="color:green"` for positive totals, `style="color:red"` for negative.
   - Account filter dropdown (same GET-form pattern as transaction list).
   - Table columns: Sell Date | Symbol | Account | Qty Sold | Sell Proceeds (CAD) |
     Cost Basis FIFO (CAD) | P&L FIFO (CAD) | Cost Basis Avg (CAD) | P&L Avg (CAD)
   - Color the P&L cells green/red using inline style.
   - Use `|floatformat:2` on all Decimal columns.
   - If `trades` is empty, show `<p>No closed positions found.</p>`.

6. **Tests in `core/tests/test_realised_pnl.py`**
   - `TestCase` class `RealisedPnlTests`.
   - `setUp`: create Account (`RPNL001`, broker=IB), BrokerStatement, and transactions:
     - BUY 10 AAPL @ cost_cad = 2000.00 (amount_cad = -2000.00), tx_hash='rpnl_buy1'
     - BUY 10 AAPL @ cost_cad = 2200.00 (amount_cad = -2200.00), tx_hash='rpnl_buy2', later date
     - SELL 15 AAPL with sell_amount_cad = 3300.00 (amount_cad = 3300.00), tx_hash='rpnl_sell1'
   - `test_fifo_cost_basis`: call `compute_realised_pnl()` directly; assert first lot cost = 2000.00 fully consumed + 5 shares of second lot at 1100.00 = 3100.00 total FIFO cost basis.
   - `test_avg_cost_basis`: average cost = 4200/20 = 210 per share; 15 × 210 = 3150.00 avg cost basis.
   - `test_realised_pnl_page_loads`: GET `/realised-pnl/` returns 200.
   - `test_realised_pnl_shows_symbol`: response contains `AAPL`.
   - `test_realised_pnl_account_filter`: with a second account that has no SELLs, filter by `RPNL001` and assert only those trades appear.
   - All BrokerStatement creates must use fields: `source`, `file_date`, `raw_filename`.
   - All Transaction creates must include `commission=Decimal('0')`.

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations.
- `compute_positions()` must remain unchanged.
- FIFO and avg-cost must be computed independently in the same function — do not share state between the two methods.
- Follow function-based view style (render, get_object_or_404, redirect).
- All templates must extend `core/base.html`.
- Type hints required on `compute_realised_pnl`.
- Run `python manage.py test core --settings=portfolio_manager.settings_test`. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: realised P&L with FIFO and average-cost methods"`

---

## Manual Steps Required

- [ ] Review and push feature branch: `git push origin feature/realised-pnl`
- [ ] Merge to dev after review.
