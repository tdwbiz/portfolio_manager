Feature Roadmap to "Complete Product"
Phase 1 — Core Usability (3–5 sessions)
These are table-stakes for a portfolio manager anyone would actually use.

#	Feature	Why
1.1	Account management UI — create/edit/delete accounts in the browser	Currently accounts only appear if data is imported; no way to set them up manually
1.2	Portfolio summary page — current holdings with cost basis, market value, unrealised P&L	The compute_positions util exists but the dashboard only shows cost, no live prices
1.3	Date-range filter on transaction list	Can't currently filter "show me Q1 2026"
1.4	Fix the 6 broken core view tests (tx_hash NOT NULL)	Test suite has known failures that mask real regressions
Phase 2 — Market Data & P&L (3–4 sessions)
#	Feature	Why
2.1	Price feed integration (e.g. yfinance or Alpha Vantage free tier) — fetch current prices for held symbols	Needed for unrealised P&L
2.2	Realised P&L calculation — FIFO or avg-cost matching of BUY→SELL pairs	Core purpose of a portfolio manager
2.3	CAD/USD FX handling — use stored exchange_rate_cad consistently for all P&L calculations	amount_cad exists on the model but may not be populated consistently
Phase 3 — Reporting (2–3 sessions)
#	Feature	Why
3.1	CSV/Excel export of transaction list (respects current filters)	Users want to pull data into Excel
3.2	Performance chart — portfolio value over time (simple line chart, Chart.js)	Visibility
3.3	Dividend/income summary — total income by symbol and year	Tax reporting
Phase 4 — Operations & Polish (1–2 sessions)
#	Feature	Why
4.1	Import history UI — view FileImportLog records with rejection log download links	Currently only visible in Django admin
4.2	Admin account setup — createsuperuser + basic auth, or lock behind Django's auth	No auth at all right now
4.3	logs in .gitignore	Rejection CSVs will contain real trade data; shouldn't be committed
Suggested Next Step