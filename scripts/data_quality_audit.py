"""Comprehensive data quality audit for RBC monthly statements.

Runs against production MSSQL database. Does NOT modify any data.
Output: markdown report in docs/reconciliation/

Usage:
    python scripts/data_quality_audit.py
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings")

import django
django.setup()

from django.db.models import Sum, Count

from core.models import (
    Account,
    RbcMonthly,
    RbcMonthlyTransaction,
    RbcMonthlyHolding,
    RbcMonthlyBalance,
    CoreMonthlyStatement,
    CoreAnnualReport,
    Transaction,
)


def section(title):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")


def subsection(title):
    print(f"\n--- {title} ---\n")


def run_audit():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lines = []

    def add(text=""):
        lines.append(text)

    add(f"# Data Quality Audit Report")
    add(f"")
    add(f"**Model:** qwen27B-code (Zed Agent)")
    add(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"**Database:** MSSQL (Production)")
    add(f"")

    # =====================================================
    # SECTION 1: Account-level checks
    # =====================================================
    section("1. Account Data Quality")

    add("## 1. Account Data Quality")
    add("")
    add(f"Total accounts: {Account.objects.count()}")
    add("")

    # Check account_id format
    add("### Account ID Format")
    add("")
    add("| # | Account ID | Name | Broker | Has dashes | Normalized |")
    add("|---|-----------|------|--------|-----------|------------|")
    for acc in Account.objects.all().order_by('account_id'):
        has_dashes = '-' in acc.account_id
        normalized = acc.account_id.replace('-', '').replace(' ', '')
        flag = "⚠️" if has_dashes else ""
        add(f"| {acc.id} | `{acc.account_id}` | {acc.name} | {acc.broker} | {has_dashes} {flag} | `{normalized}` |")
    add("")

    # =====================================================
    # SECTION 2: RbcMonthly checks
    # =====================================================
    section("2. RBC Monthly Statement Data")

    add("## 2. RBC Monthly Statement Data")
    add("")
    add(f"Total RbcMonthly records: {RbcMonthly.objects.count()}")
    add("")

    # 2a. Account number inconsistencies
    subsection("2a. Account Number Inconsistencies")
    add("### 2a. Account Number Inconsistencies")
    add("")

    account_numbers = RbcMonthly.objects.values_list('account_number', 'account_id').distinct()
    add("| Account ID | Account Number | Has dashes | Normalized |")
    add("|-----------|---------------|-----------|------------|")
    for an, aid in sorted(set(account_numbers)):
        if not an:
            continue
        has_dashes = '-' in an
        normalized = an.replace('-', '').replace(' ', '')
        flag = "⚠️" if has_dashes else ""
        add(f"| {aid} | `{an}` | {has_dashes} {flag} | `{normalized}` |")
    add("")

    # 2b. Statements with null last_statement_date
    subsection("2b. Null last_statement_date")
    add("### 2b. Statements with NULL last_statement_date")
    add("")
    null_ls = RbcMonthly.objects.filter(last_statement_date__isnull=True)
    add(f"Count: {null_ls.count()}")
    add("")
    add("| ID | Account | Statement Date | Currency | File |")
    add("|----|---------|---------------|----------|------|")
    for s in null_ls.order_by('account', 'statement_date')[:50]:
        add(f"| {s.id} | {s.account.account_id} | {s.statement_date} | {s.currency} | {s.file_name[:40]} |")
    if null_ls.count() > 50:
        add(f"... and {null_ls.count() - 50} more")
    add("")

    # 2c. Statement date coverage per account
    subsection("2c. Statement Date Coverage")
    add("### 2c. Statement Date Coverage Per Account")
    add("")
    add("| Account | Statements | Date Range | Gaps (months) |")
    add("|---------|-----------|------------|---------------|")

    for acc in Account.objects.filter(rbc_monthly_statements__isnull=False).distinct():
        stmts = RbcMonthly.objects.filter(account=acc).order_by('statement_date')
        if not stmts.exists():
            continue
        count = stmts.count()
        first = stmts.first().statement_date
        last = stmts.last().statement_date

        # Find gaps
        months_seen = set()
        for s in stmts:
            months_seen.add((s.statement_date.year, s.statement_date.month))
        expected_months = set()
        y, m = first.year, first.month
        while (y, m) <= (last.year, last.month):
            expected_months.add((y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1
        gaps = expected_months - months_seen

        add(f"| {acc.account_id} | {count} | {first} → {last} | {len(gaps)} {'⚠️' if gaps else '✓'} |")
        if gaps:
            for gy, gm in sorted(gaps)[:5]:
                add(f"  - Missing: {gy}-{gm:02d}")
    add("")

    # 2d. Duplicate statement dates
    subsection("2d. Duplicate Statement Dates")
    add("### 2d. Duplicate (account, statement_date, currency) combinations")
    add("")
    dupes = (
        RbcMonthly.objects.values('account', 'statement_date', 'currency')
        .annotate(count=Count('id'))
        .filter(count__gt=1)
    )

    if dupes.exists():
        add("| Account | Date | Currency | Count |")
        add("|---------|------|----------|-------|")
        for d in dupes:
            add(f"| {d['account']} | {d['statement_date']} | {d['currency']} | {d['count']} |")
    else:
        add("No duplicates found ✓")
    add("")

    # =====================================================
    # SECTION 3: RbcMonthlyTransaction checks
    # =====================================================
    section("3. RBC Monthly Transaction Data")

    add("## 3. RBC Monthly Transaction Data")
    add("")
    add(f"Total transactions: {RbcMonthlyTransaction.objects.count()}")
    add("")

    # 3a. Transactions with empty symbol
    subsection("3a. Transactions with Empty Symbol")
    add("### 3a. Transactions where symbol is empty (activity requires symbol)")
    add("")
    empty_sym = RbcMonthlyTransaction.objects.filter(
        symbol__exact='',
        activity_type__in=['BOUGHT', 'SOLD', 'DIVIDEND', 'DIV REIN', 'DIST', 'INTEREST_RECEIVED']
    )
    add(f"Count: {empty_sym.count()}")
    add("")

    if empty_sym.exists():
        add("| ID | Date | Activity | Description | Statement |")
        add("|----|------|----------|-------------|-----------|")
        for tx in empty_sym.order_by('date')[:100]:
            desc_short = tx.description[:60] if tx.description else ""
            add(f"| {tx.id} | {tx.date} | {tx.activity_type} | `{desc_short}` | {tx.rbc_monthly.id} ({tx.rbc_monthly.statement_date}) |")
        if empty_sym.count() > 100:
            add(f"... and {empty_sym.count() - 100} more")
    add("")

    # 3b. Transactions with zero debit AND zero credit (non-zero-activity types)
    subsection("3b. Transactions with Zero Debit AND Zero Credit")
    add("### 3b. Transactions with debit=0 AND credit=0")
    add("")
    zero_both = RbcMonthlyTransaction.objects.filter(debit=0, credit=0)
    add(f"Total zero-both: {zero_both.count()}")
    add("")

    # Break down by activity type
    zero_by_type = {}
    for tx in zero_both.values('activity_type').annotate(count=Count('id')):
        zero_by_type[tx['activity_type']] = tx['count']
    add("| Activity Type | Count | Expected Zero? |")
    add("|-------------|-------|---------------|")
    expected_zero_types = {'DIV REIN', 'INTEREST_RECEIVED', 'ADJUST', 'RTC', 'OTHER'}
    for at, cnt in sorted(zero_by_type.items(), key=lambda x: -x[1]):
        expected = "✓" if at in expected_zero_types else "⚠️ UNEXPECTED"
        add(f"| {at} | {cnt} | {expected} |")
    add("")

    # 3c. Transactions where debit > 0 AND credit > 0
    subsection("3c. Transactions with Both Debit AND Credit > 0")
    add("### 3c. Transactions where debit > 0 AND credit > 0 (suspicious)")
    add("")
    both_positive = RbcMonthlyTransaction.objects.filter(debit__gt=0, credit__gt=0)
    add(f"Count: {both_positive.count()}")
    if both_positive.exists():
        add("| ID | Date | Activity | Description | Debit | Credit |")
        add("|----|------|----------|-------------|-------|--------|")
        for tx in both_positive[:20]:
            add(f"| {tx.id} | {tx.date} | {tx.activity_type} | `{tx.description[:50]}` | {tx.debit} | {tx.credit} |")
    add("")

    # 3d. BOUGHT transactions with credit > 0 (should be debit)
    subsection("3d. BOUGHT with credit > 0 / SOLD with debit > 0")
    add("### 3d. Cash flow direction mismatches")
    add("")
    bought_credit = RbcMonthlyTransaction.objects.filter(activity_type='BOUGHT', credit__gt=0)
    sold_debit = RbcMonthlyTransaction.objects.filter(activity_type='SOLD', debit__gt=0)
    add(f"BOUGHT with credit > 0: {bought_credit.count()} {'⚠️' if bought_credit.count() else '✓'}")
    add(f"SOLD with debit > 0: {sold_debit.count()} {'⚠️' if sold_debit.count() else '✓'}")
    add("")

    # 3e. Transaction count per statement
    subsection("3e. Transaction Count Per Statement")
    add("### 3e. Statements with 0 transactions")
    add("")
    stmt_ids_with_tx = set(RbcMonthlyTransaction.objects.values_list('rbc_monthly_id', flat=True).distinct())
    all_stmt_ids = set(RbcMonthly.objects.values_list('id', flat=True))
    no_tx = all_stmt_ids - stmt_ids_with_tx
    add(f"Statements with 0 transactions: {len(no_tx)}")
    if no_tx:
        add("| ID | Account | Statement Date | Currency |")
        add("|----|---------|---------------|----------|")
        for sid in sorted(no_tx):
            s = RbcMonthly.objects.get(id=sid)
            add(f"| {s.id} | {s.account.account_id} | {s.statement_date} | {s.currency} |")
    add("")

    # =====================================================
    # SECTION 4: RbcMonthlyHolding checks
    # =====================================================
    section("4. RBC Monthly Holding Data")

    add("## 4. RBC Monthly Holding Data")
    add("")
    add(f"Total holdings: {RbcMonthlyHolding.objects.count()}")
    add("")

    # 4a. Holdings with empty symbol
    subsection("4a. Holdings with Empty Symbol")
    add("### 4a. Holdings where symbol is empty")
    add("")
    empty_hsym = RbcMonthlyHolding.objects.filter(symbol__exact='')
    add(f"Count: {empty_hsym.count()} out of {RbcMonthlyHolding.objects.count()}")
    add("")
    if empty_hsym.exists():
        add("| ID | Security Name | Quantity | Market Value | Statement |")
        add("|----|-------------|----------|-------------|-----------|")
        for h in empty_hsym.order_by('security')[:50]:
            add(f"| {h.id} | `{h.security[:60]}` | {h.quantity} | {h.market_value} | {h.rbc_monthly.statement_date} |")
    add("")

    # 4b. Security name vs symbol redundancy analysis
    subsection("4b. Security vs Symbol Redundancy")
    add("### 4b. Security Name vs Symbol — Redundancy Analysis")
    add("")
    add("The `security` field contains the full security name (e.g., 'RBC BLUE CHIP FUND'). The `symbol` field contains the ticker (e.g., 'RBF2010'). They serve different purposes:")
    add("- `symbol`: Used for price lookup via yfinance, position matching, grouping")
    add("- `security`: Human-readable display name, contains fund/company full name")
    add("")
    add("However, when `symbol` is empty, `security` is the only identifier, which breaks position matching.")
    add("")

    # 4c. Holdings with zero quantity
    subsection("4c. Holdings with Zero Quantity")
    add("### 4c. Holdings where quantity = 0")
    add("")
    zero_qty = RbcMonthlyHolding.objects.filter(quantity=0)
    add(f"Count: {zero_qty.count()}")
    if zero_qty.exists():
        add("| ID | Security | Symbol | Statement |")
        add("|----|----------|--------|-----------|")
        for h in zero_qty[:20]:
            add(f"| {h.id} | `{h.security[:50]}` | `{h.symbol}` | {h.rbc_monthly.statement_date} |")
    add("")

    # 4d. Holdings with zero market value but non-zero quantity
    subsection("4d. Holdings with Market Value = 0 but Quantity > 0")
    add("### 4d. Holdings where market_value = 0 but quantity > 0")
    add("")
    mv_zero = RbcMonthlyHolding.objects.filter(market_value=0, quantity__gt=0)
    add(f"Count: {mv_zero.count()} {'⚠️' if mv_zero.count() else '✓'}")
    if mv_zero.exists():
        add("| ID | Security | Symbol | Quantity | Statement |")
        add("|----|----------|--------|----------|-----------|")
        for h in mv_zero[:20]:
            add(f"| {h.id} | `{h.security[:50]}` | `{h.symbol}` | {h.quantity} | {h.rbc_monthly.statement_date} |")
    add("")

    # =====================================================
    # SECTION 5: Cross-table reconciliation
    # =====================================================
    section("5. Cross-Table Reconciliation")

    add("## 5. Cross-Table Reconciliation")
    add("")

    # 5a. Holdings vs Transactions symbol match
    subsection("5a. Symbols in Transactions but NOT in Holdings (and vice versa)")
    add("### 5a. Symbol Coverage: Transactions vs Holdings Per Statement")
    add("")

    statements_to_check = RbcMonthly.objects.filter(
        transactions__isnull=False
    ).distinct().order_by('-statement_date')[:24]  # Last 24 statements

    add("| Statement | Date | Tx Symbols | Holding Symbols | In Tx Not In Holding | In Holding Not In Tx |")
    add("|-----------|------|-----------|----------------|---------------------|---------------------|")

    for s in statements_to_check:
        tx_symbols = set(
            RbcMonthlyTransaction.objects.filter(rbc_monthly=s, symbol__gt='')
            .values_list('symbol', flat=True)
            .distinct()
        )
        h_symbols = set(
            RbcMonthlyHolding.objects.filter(rbc_monthly=s, symbol__gt='')
            .values_list('symbol', flat=True)
            .distinct()
        )
        tx_only = tx_symbols - h_symbols
        h_only = h_symbols - tx_symbols

        flag = "⚠️" if tx_only or h_only else "✓"
        add(f"| {s.id} | {s.statement_date} | {len(tx_symbols)} | {len(h_symbols)} | {len(tx_only)} | {len(h_only)} | {flag}")
        if tx_only:
            add(f"  Tx-only symbols: {', '.join(sorted(tx_only))}")
        if h_only:
            add(f"  Holding-only symbols: {', '.join(sorted(h_only))}")
    add("")

    # 5b. Balance sum vs end_total_market_value
    subsection("5b. Balance Sum vs. end_total_market_value")
    add("### 5b. Statement end_total_market_value vs. Sum of RbcMonthlyBalance.closing_balance")
    add("")

    add("| Statement | Date | Currency | end_total_mv | Sum(closing_balance) | Diff |")
    add("|-----------|------|----------|-------------|---------------------|------|")

    for s in RbcMonthly.objects.order_by('-statement_date')[:48]:
        balance_sum = RbcMonthlyBalance.objects.filter(rbc_monthly=s).aggregate(
            total=Sum('closing_balance')
        )['total'] or Decimal('0')
        diff = abs(s.end_total_market_value - balance_sum)
        flag = "⚠️" if diff > 100 else "✓"
        add(f"| {s.id} | {s.statement_date} | {s.currency} | {s.end_total_market_value} | {balance_sum} | {diff} {flag} |")
    add("")

    # 5c. Holding market_value sum vs end_total_market_value
    subsection("5c. Holding Market Value Sum vs. end_total_market_value")
    add("### 5c. Sum of Holding market_value vs. end_total_market_value")
    add("")

    add("| Statement | Date | Currency | end_total_mv | Sum(holding_mv) | Diff |")
    add("|-----------|------|----------|-------------|---------------|------|")

    for s in RbcMonthly.objects.order_by('-statement_date')[:48]:
        holding_sum = RbcMonthlyHolding.objects.filter(rbc_monthly=s, currency=s.currency).aggregate(
            total=Sum('market_value')
        )['total'] or Decimal('0')
        diff = abs(s.end_total_market_value - holding_sum)
        flag = "⚠️" if diff > 100 else "✓"
        add(f"| {s.id} | {s.statement_date} | {s.currency} | {s.end_total_market_value} | {holding_sum} | {diff} {flag} |")
    add("")

    # 5d. Transaction net flow vs balance change
    subsection("5d. Transaction Net Flow vs. Balance Change")
    add("### 5d. Consecutive Statement Balance Change vs. Transaction Net Flow")
    add("")

    for acc in Account.objects.filter(rbc_monthly_statements__isnull=False).distinct():
        stmts = list(
            RbcMonthly.objects.filter(account=acc)
            .order_by('statement_date')
        )
        if len(stmts) < 2:
            continue

        add(f"**Account: {acc.account_id}**")
        add("")
        add("| Date | Currency | Δ Balance | Net Flow (credit-debit) | Difference |")
        add("|------|----------|-----------|------------------------|------------|")

        for i in range(1, len(stmts)):
            prev = stmts[i-1]
            curr = stmts[i]
            if prev.currency != curr.currency:
                continue

            delta_balance = curr.end_total_market_value - prev.end_total_market_value
            net_flow = (
                RbcMonthlyTransaction.objects.filter(rbc_monthly=curr)
                .aggregate(
                    net=Sum('credit') - Sum('debit')
                )['net'] or Decimal('0')
            )
            diff = abs(delta_balance - net_flow)
            flag = "⚠️" if diff > 500 else ""
            add(f"| {curr.statement_date} | {curr.currency} | {delta_balance} | {net_flow} | {diff} {flag} |")
        add("")
    add("")

    # =====================================================
    # SECTION 6: CSV Transaction vs RBC Transaction
    # =====================================================
    section("6. CSV Transaction (core_transaction) Overview")

    add("## 6. CSV Transaction (core_transaction) Overview")
    add("")
    add(f"Total CSV transactions: {Transaction.objects.count()}")
    add("")

    # 6a. CSV transactions with empty symbol
    subsection("6a. CSV Transactions with Empty Symbol")
    add("### 6a. CSV transactions where symbol is empty")
    add("")
    csv_empty = Transaction.objects.filter(symbol__exact='')
    add(f"Count: {csv_empty.count()}")
    if csv_empty.exists():
        by_type = {}
        for tx in csv_empty.values('type').annotate(count=Count('id')):
            by_type[tx['type']] = tx['count']
        add("| Type | Count |")
        add("|------|-------|")
        for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
            add(f"| {t} | {c} |")
    add("")

    # 6b. CoreMonthlyStatement coverage
    subsection("6b. CoreMonthlyStatement Coverage")
    add("### 6b. CoreMonthlyStatement vs. RbcMonthly coverage")
    add("")
    add(f"CoreMonthlyStatement records: {CoreMonthlyStatement.objects.count()}")
    add(f"CoreAnnualReport records: {CoreAnnualReport.objects.count()}")
    add("")

    for acc in Account.objects.all().order_by('account_id'):
        rbc_count = RbcMonthly.objects.filter(account=acc).count()
        cms_count = CoreMonthlyStatement.objects.filter(account=acc).count()
        ar_count = CoreAnnualReport.objects.filter(account=acc).count()
        add(f"| {acc.account_id} | RBC: {rbc_count} | CMS: {cms_count} | AR: {ar_count} | {'⚠️' if cms_count < rbc_count else '✓'} |")
    add("")

    # =====================================================
    # SECTION 7: Summary of Issues
    # =====================================================
    section("7. Summary")

    add("## 7. Summary of Issues Found")
    add("")
    add("### Critical (data integrity)")
    add("- [ ] Account numbers with dashes — inconsistent with Account.account_id")
    add("- [ ] Transactions with empty symbol where symbol is required")
    add("- [ ] Holdings with empty symbol — breaks position matching")
    add("")
    add("### Moderate (data quality)")
    add("- [ ] Statements with NULL last_statement_date")
    add("- [ ] Statements with 0 transactions")
    add("- [ ] Symbol mismatches between transactions and holdings")
    add("- [ ] Balance sum discrepancies")
    add("")
    add("### Low (informational)")
    add("- [ ] Holdings with zero quantity")
    add("- [ ] Holdings with zero market value but non-zero quantity")
    add("- [ ] Missing statement months (gaps in coverage)")

    # Write report
    report_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "reconciliation",
        f"data_quality_audit_qwen27B_{timestamp}.md"
    )
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"\n{'='*80}")
    print(f"  REPORT SAVED: {report_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    run_audit()
