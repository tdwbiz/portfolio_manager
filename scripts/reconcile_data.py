"""Comprehensive data reconciliation report generator.

Reads from the production database (MSSQL) and generates .md reports:
  1. recon_report_1_rbc_monthly_vs_annual.md   — RbcMonthly vs RbcAnnual
  2. recon_report_2_rbc_monthly_vs_csv.md       — RbcMonthly vs CoreMonthlyStatement (CSV)
  3. recon_report_3_data_continuity.md           — Earliest date of continuous data per account
  4. recon_report_4_proposal.md                  — Proposal to fix gaps

**Scope:** RBC accounts only. IB accounts are noted but not analyzed.

**Gap definition (RbcMonthly):** RBC only issues monthly statements when there
is activity. A missing month between two consecutive RbcMonthly records is:
  - **No-activity skip** if end_total_market_value of the earlier record equals
    begin_total_market_value of the later record (within $1). RBC simply didn't
    send a statement because nothing changed.
  - **True gap** if the balances differ — meaning activity occurred but we don't
    have the PDF imported.

Usage:
    python reconcile_data.py  (runs its own Django setup)
"""

import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# Ensure project root is on sys.path
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent  # portfolio_manager/
sys.path.insert(0, str(project_root))

os.chdir(project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')

import django
django.setup()

from core.models import (
    Account, AccountCurrency, RbcMonthly, RbcAnnual,
    CoreMonthlyStatement, CoreAnnualReport, Transaction,
    RbcMonthlyBalance, RbcMonthlyTransaction, RbcMonthlyHolding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def month_key(d):
    """Return (year, month) from a date."""
    return (d.year, d.month)


def month_range(start_date, end_date):
    """Yield (year, month) for every month between start_date and end_date inclusive."""
    y, m = start_date.year, start_date.month
    ey, em = end_date.year, end_date.month
    while (y, m) <= (ey, em):
        yield (y, m)
        m += 1
        if m > 12:
            m = 1
            y += 1


def month_label(ym):
    """Return 'YYYY-MM' string."""
    return f"{ym[0]:04d}-{ym[1]:02d}"


def fmt(val, precision=2):
    """Format a Decimal/number for display."""
    if val is None:
        return "N/A"
    try:
        return f"{Decimal(str(val)):,.{precision}f}"
    except Exception:
        return str(val)


def fmt_pct(val):
    """Format percentage."""
    if val is None:
        return "N/A"
    try:
        return f"{Decimal(str(val)):.2f}%"
    except Exception:
        return str(val)


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

def find_longest_continuous_run(months_set):
    """Find the longest consecutive run of months in a set of (year, month) tuples."""
    if not months_set:
        return None, None, 0

    sorted_months = sorted(months_set)
    best_start, best_end, best_len = sorted_months[0], sorted_months[0], 1
    cur_start, cur_end, cur_len = sorted_months[0], sorted_months[0], 1

    for i in range(1, len(sorted_months)):
        prev = sorted_months[i - 1]
        curr = sorted_months[i]
        # Check if consecutive
        expected = (prev[0], prev[1] + 1)
        if prev[1] == 12:
            expected = (prev[0] + 1, 1)
        if curr == expected:
            cur_end = curr
            cur_len += 1
        else:
            if cur_len > best_len:
                best_start, best_end, best_len = cur_start, cur_end, cur_len
            cur_start, cur_end, cur_len = curr, curr, 1

    if cur_len > best_len:
        best_start, best_end, best_len = cur_start, cur_end, cur_len

    return best_start, best_end, best_len


def find_gaps_raw(months_set, start_ym, end_ym):
    """Find all missing months in the range [start_ym, end_ym]."""
    missing = []
    for ym in month_range(
        date(start_ym[0], start_ym[1], 1),
        date(end_ym[0], end_ym[1], 1),
    ):
        if ym not in months_set:
            missing.append(ym)
    return missing


def classify_rbc_monthly_gaps(sorted_records, months_set):
    """Classify missing months between consecutive RbcMonthly records.

    Args:
        sorted_records: list of (month_key, RbcMonthly) sorted by month_key
        months_set: set of month_keys that have records

    Returns:
        (true_gaps, no_activity_skips) — both lists of (year, month) tuples
    """
    if len(sorted_records) < 2:
        return [], []

    true_gaps = []
    no_activity_skips = []

    for i in range(len(sorted_records) - 1):
        mk_prev, rec_prev = sorted_records[i]
        mk_next, rec_next = sorted_records[i + 1]

        # Find all missing months between these two records
        gap_months = find_gaps_raw(months_set, mk_prev, mk_next)
        if not gap_months:
            continue

        # Check if this is a no-activity skip
        end_val = rec_prev.end_total_market_value or Decimal('0')
        begin_val = rec_next.begin_total_market_value or Decimal('0')
        diff = abs(end_val - begin_val)

        if diff <= Decimal('1.00'):
            # Balance unchanged — RBC likely didn't issue a statement
            no_activity_skips.extend(gap_months)
        else:
            # Balance changed — activity occurred but we don't have the PDF
            true_gaps.extend(gap_months)

    return true_gaps, no_activity_skips


# ---------------------------------------------------------------------------
# Gather data
# ---------------------------------------------------------------------------

def gather_account_data():
    """Build a comprehensive data structure per account per currency.

    Only RBC accounts are included. IB accounts are tracked separately.
    """
    data = {}  # {(account_pk, currency): {...}}
    ib_accounts = []  # List of (account_id, currency) for IB accounts

    accounts = Account.objects.all().order_by('pk')
    for ac in accounts:
        currencies = list(ac.currencies.values_list('currency', flat=True))
        for cur in currencies:
            key = (ac.pk, cur)
            if ac.broker == 'RBC':
                data[key] = {
                    'account_id': ac.account_id,
                    'account_name': ac.name,
                    'account_pk': ac.pk,
                    'currency': cur,
                    'broker': 'RBC',
                    # RbcMonthly
                    'rbcm_months': set(),
                    'rbcm_min_date': None,
                    'rbcm_max_date': None,
                    'rbcm_count': 0,
                    'rbcm_records': {},  # month_key -> RbcMonthly record
                    # RbcAnnual
                    'rbca_years': set(),
                    'rbca_count': 0,
                    'rbca_records': {},  # year -> RbcAnnual record
                    # CoreMonthlyStatement (CSV)
                    'cms_months': set(),
                    'cms_min_date': None,
                    'cms_max_date': None,
                    'cms_count': 0,
                    'cms_records': {},  # month_key -> CoreMonthlyStatement record
                }
            else:
                ib_accounts.append((ac.account_id, ac.name, cur))

    # Load RbcMonthly
    for rec in RbcMonthly.objects.select_related('account').all():
        key = (rec.account_id, rec.currency)
        if key in data:
            mk = month_key(rec.statement_date)
            data[key]['rbcm_months'].add(mk)
            data[key]['rbcm_records'][mk] = rec
            data[key]['rbcm_count'] += 1
            if data[key]['rbcm_min_date'] is None or rec.statement_date < data[key]['rbcm_min_date']:
                data[key]['rbcm_min_date'] = rec.statement_date
            if data[key]['rbcm_max_date'] is None or rec.statement_date > data[key]['rbcm_max_date']:
                data[key]['rbcm_max_date'] = rec.statement_date

    # Load RbcAnnual
    for rec in RbcAnnual.objects.select_related('account').all():
        key = (rec.account_id, rec.currency)
        if key in data:
            data[key]['rbca_years'].add(rec.report_year)
            data[key]['rbca_records'][rec.report_year] = rec
            data[key]['rbca_count'] += 1

    # Load CoreMonthlyStatement
    for rec in CoreMonthlyStatement.objects.select_related('account').all():
        key = (rec.account_id, rec.currency)
        if key in data:
            mk = month_key(rec.statement_month)
            data[key]['cms_months'].add(mk)
            data[key]['cms_records'][mk] = rec
            data[key]['cms_count'] += 1
            if data[key]['cms_min_date'] is None or rec.statement_month < data[key]['cms_min_date']:
                data[key]['cms_min_date'] = rec.statement_month
            if data[key]['cms_max_date'] is None or rec.statement_month > data[key]['cms_max_date']:
                data[key]['cms_max_date'] = rec.statement_month

    return data, ib_accounts


# ---------------------------------------------------------------------------
# Report 1: RbcMonthly vs RbcAnnual
# ---------------------------------------------------------------------------

def generate_rbc_monthly_vs_annual(data):
    """Compare RbcMonthly year-end balances against RbcAnnual."""
    from datetime import datetime
    lines = []
    lines.append(f"# Report 1: RbcMonthly vs RbcAnnual Reconciliation\n")
    lines.append(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append("Compares December year-end `RbcMonthly` balances against `RbcAnnual` records.\n")
    lines.append("**Threshold:** Balances within $1.00 are considered matching.\n")
    lines.append("---\n")

    total_compared = 0
    total_matched = 0
    total_mismatched = 0
    total_no_annual = 0
    total_no_monthly = 0

    for (ac_pk, cur), d in sorted(data.items()):
        if not d['rbcm_months'] and not d['rbca_years']:
            continue

        lines.append(f"## {d['account_id']} ({d['account_name']}) — {cur}\n")

        rbcm_years = set()
        for mk in d['rbcm_months']:
            if mk[1] == 12:  # December
                rbcm_years.add(mk[0])

        rbca_years = d['rbca_years']
        all_years = sorted(rbcm_years | rbca_years)

        if not all_years:
            lines.append("No year-end data to compare.\n")
            continue

        table_header = f"{'Year':>6} | {'RbcMonthly (Dec)':>18} | {'RbcAnnual':>18} | {'Diff':>12} | Status"
        table_sep = "-" * 6 + "-|--" * 4 + "-|--"
        lines.append(f"{table_header}")
        lines.append(f"{table_sep}")

        for year in all_years:
            has_rbcm = year in rbcm_years
            has_rbca = year in rbca_years

            if has_rbcm and has_rbca:
                total_compared += 1
                rbm = d['rbcm_records'].get((year, 12))
                rba = d['rbca_records'].get(year)

                if rbm and rba:
                    rbm_end = rbm.end_total_market_value or Decimal('0')
                    rba_end = rba.bal_ending_value or Decimal('0')
                    diff = abs(rbm_end - rba_end)
                    matched = diff <= Decimal('1.00')

                    if matched:
                        total_matched += 1
                        status = "OK"
                    else:
                        total_mismatched += 1
                        status = f"MISMATCH"

                    lines.append(
                        f"{year:>6} | {fmt(rbm_end):>18} | {fmt(rba_end):>18} | {fmt(diff):>12} | {status}"
                    )
                else:
                    lines.append(f"{year:>6} | N/A | N/A | N/A | DATA_ERROR")
            elif has_rbcm:
                total_no_annual += 1
                rbm = d['rbcm_records'].get((year, 12))
                val = fmt(rbm.end_total_market_value) if rbm else "N/A"
                lines.append(f"{year:>6} | {val:>18} | {'(missing)':>18} | {'—':>12} | NO RbcAnnual")
            else:
                total_no_monthly += 1
                rba = d['rbca_records'].get(year)
                val = fmt(rba.bal_ending_value) if rba else "N/A"
                lines.append(f"{year:>6} | {'(missing)':>18} | {val:>18} | {'—':>12} | NO RbcMonthly (Dec)")

        lines.append("")

    # Summary
    lines.append("---\n")
    lines.append("## Summary\n")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Years compared (both present) | {total_compared} |")
    lines.append(f"| Balanced within $1.00 | {total_matched} |")
    lines.append(f"| Mismatch (> $1.00) | {total_mismatched} |")
    lines.append(f"| Has RbcMonthly (Dec) but no RbcAnnual | {total_no_annual} |")
    lines.append(f"| Has RbcAnnual but no RbcMonthly (Dec) | {total_no_monthly} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report 2: RbcMonthly vs CoreMonthlyStatement (CSV)
# ---------------------------------------------------------------------------

def generate_rbc_monthly_vs_csv(data):
    """Compare RbcMonthly ending balances against CoreMonthlyStatement."""
    from datetime import datetime
    lines = []
    lines.append(f"# Report 2: RbcMonthly vs CoreMonthlyStatement (CSV) Reconciliation\n")
    lines.append(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append("Compares monthly ending balances from PDF import (RbcMonthly) against CSV-derived CoreMonthlyStatement.\n")
    lines.append("**Threshold:** Balances within $1.00 are considered matching.\n")
    lines.append("---\n")

    total_compared = 0
    total_matched = 0
    total_mismatched = 0
    total_no_csv = 0
    total_no_pdf = 0

    for (ac_pk, cur), d in sorted(data.items()):
        if not d['rbcm_months'] and not d['cms_months']:
            continue

        lines.append(f"## {d['account_id']} ({d['account_name']}) — {cur}\n")

        all_months = sorted(d['rbcm_months'] | d['cms_months'])
        if not all_months:
            lines.append("No overlapping data.\n")
            continue

        # Only show a sample if too many months
        show_all = len(all_months) <= 36
        shown_months = all_months if show_all else (all_months[:6] + ['...'] + all_months[-6:])

        table_header = f"{'Month':>10} | {'RbcMonthly':>16} | {'CoreMonthlyStmt':>16} | {'Diff':>12} | Status"
        table_sep = "-" * 10 + "-|--" * 4 + "-|--"
        lines.append(f"{table_header}")
        lines.append(f"{table_sep}")

        for item in shown_months:
            if item == '...':
                lines.append(f"{'...':>10} | {'...':>16} | {'...':>16} | {'...':>12} | {'...'}")
                continue

            mk = item
            has_rbcm = mk in d['rbcm_months']
            has_cms = mk in d['cms_months']

            if has_rbcm and has_cms:
                total_compared += 1
                rbm = d['rbcm_records'][mk]
                cms = d['cms_records'][mk]

                rbm_end = rbm.end_total_market_value or Decimal('0')
                cms_end = cms.ending_market_value or Decimal('0')
                diff = abs(rbm_end - cms_end)
                matched = diff <= Decimal('1.00')

                if matched:
                    total_matched += 1
                    status = "OK"
                else:
                    total_mismatched += 1
                    status = f"MISMATCH"

                lines.append(
                    f"{month_label(mk):>10} | {fmt(rbm_end):>16} | {fmt(cms_end):>16} | {fmt(diff):>12} | {status}"
                )
            elif has_rbcm:
                total_no_csv += 1
                rbm = d['rbcm_records'][mk]
                val = fmt(rbm.end_total_market_value)
                lines.append(f"{month_label(mk):>10} | {val:>16} | {'(missing)':>16} | {'—':>12} | NO CSV")
            else:
                total_no_pdf += 1
                cms = d['cms_records'][mk]
                val = fmt(cms.ending_market_value)
                lines.append(f"{month_label(mk):>10} | {'(missing)':>16} | {val:>16} | {'—':>12} | NO PDF")

        if not show_all:
            lines.append(f"\n*Showing first 6 and last 6 of {len(all_months)} months.*\n")
        lines.append("")

    # Summary
    lines.append("---\n")
    lines.append("## Summary\n")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Months compared (both present) | {total_compared} |")
    lines.append(f"| Balanced within $1.00 | {total_matched} |")
    lines.append(f"| Mismatch (> $1.00) | {total_mismatched} |")
    lines.append(f"| Has RbcMonthly but no CoreMonthlyStatement | {total_no_csv} |")
    lines.append(f"| Has CoreMonthlyStatement but no RbcMonthly | {total_no_pdf} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report 3: Data Continuity Analysis
# ---------------------------------------------------------------------------

def generate_continuity_report(data, ib_accounts):
    """Find earliest date of continuous data for each source.

    RbcMonthly gaps are classified as:
      - **True gap**: balance changed between surrounding records (PDF likely exists but not imported)
      - **No-activity skip**: balance unchanged (RBC didn't issue a statement)
    """
    from datetime import datetime
    lines = []
    lines.append(f"# Report 3: Data Continuity Analysis\n")
    lines.append(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append("For each RBC account and currency, identifies:\n")
    lines.append("- Range of available data\n")
    lines.append("- Longest continuous run (no gaps)\n")
    lines.append("- **True gaps** — missing months where balance changed (PDF likely not imported)\n")
    lines.append("- **No-activity skips** — missing months where balance was unchanged (RBC didn't issue statement)\n")
    lines.append("- Earliest month where continuous data begins (to present)\n")
    lines.append("---\n")
    lines.append("")

    # Find the latest month across all data (reference point)
    latest_month = None

    for (ac_pk, cur), d in sorted(data.items()):
        lines.append(f"## {d['account_id']} ({d['account_name']}) — {cur}\n")

        # RbcMonthly continuity
        if d['rbcm_months']:
            rbcm_sorted = sorted(d['rbcm_months'])
            rbcm_min = rbcm_sorted[0]
            rbcm_max = rbcm_sorted[-1]
            if latest_month is None or rbcm_max > latest_month:
                latest_month = rbcm_max

            # Build sorted list of (month_key, record)
            sorted_recs = [(mk, d['rbcm_records'][mk]) for mk in rbcm_sorted]

            # Longest continuous run
            run_start, run_end, run_len = find_longest_continuous_run(d['rbcm_months'])

            # Classify gaps
            true_gaps, no_activity_skips = classify_rbc_monthly_gaps(sorted_recs, d['rbcm_months'])

            # Continuous from when to latest?
            continuous_from = None
            for ym in rbcm_sorted:
                missing = find_gaps_raw(d['rbcm_months'], ym, rbcm_max)
                if not missing:
                    continuous_from = ym
                    break

            lines.append(f"### RbcMonthly (PDF)\n")
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Total months | {d['rbcm_count']} |")
            lines.append(f"| Date range | {month_label(rbcm_min)} to {month_label(rbcm_max)} |")
            lines.append(f"| Longest continuous run | {month_label(run_start)} to {month_label(run_end)} ({run_len} months) |")
            lines.append(f"| True gaps | {len(true_gaps)} |")
            lines.append(f"| No-activity skips | {len(no_activity_skips)} |")
            if continuous_from:
                lines.append(f"| Continuous from | {month_label(continuous_from)} to {month_label(rbcm_max)} |")
            else:
                lines.append(f"| Continuous from | GAPS exist — no continuous run to end |")

            if true_gaps:
                gap_strs = [month_label(g) for g in true_gaps[:10]]
                if len(true_gaps) > 10:
                    gap_strs.append(f"... +{len(true_gaps)-10} more")
                lines.append(f"| True gap months | {', '.join(gap_strs)} |")

            if no_activity_skips:
                skip_strs = [month_label(s) for s in no_activity_skips[:5]]
                if len(no_activity_skips) > 5:
                    skip_strs.append(f"... +{len(no_activity_skips)-5} more")
                lines.append(f"| No-activity skip months | {', '.join(skip_strs)} |")

            lines.append("")

        # RbcAnnual continuity
        if d['rbca_years']:
            rbca_sorted = sorted(d['rbca_years'])
            lines.append(f"### RbcAnnual (PDF)\n")
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Total years | {d['rbca_count']} |")
            lines.append(f"| Year range | {rbca_sorted[0]} to {rbca_sorted[-1]} |")

            # Find missing years
            all_years = set(range(rbca_sorted[0], rbca_sorted[-1] + 1))
            missing_years = sorted(all_years - d['rbca_years'])
            if missing_years:
                lines.append(f"| Missing years | {', '.join(map(str, missing_years))} |")
            else:
                lines.append(f"| Missing years | None — continuous |")
            lines.append("")

        # CoreMonthlyStatement (CSV) continuity
        if d['cms_months']:
            cms_sorted = sorted(d['cms_months'])
            cms_min = cms_sorted[0]
            cms_max = cms_sorted[-1]

            run_start, run_end, run_len = find_longest_continuous_run(d['cms_months'])
            gaps = find_gaps_raw(d['cms_months'], cms_min, cms_max)

            continuous_from = None
            for ym in cms_sorted:
                missing = find_gaps_raw(d['cms_months'], ym, cms_max)
                if not missing:
                    continuous_from = ym
                    break

            lines.append(f"### CoreMonthlyStatement (CSV)\n")
            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Total months | {d['cms_count']} |")
            lines.append(f"| Date range | {month_label(cms_min)} to {month_label(cms_max)} |")
            lines.append(f"| Longest continuous run | {month_label(run_start)} to {month_label(run_end)} ({run_len} months) |")
            lines.append(f"| Gaps in range | {len(gaps)} |")
            if continuous_from:
                lines.append(f"| Continuous from | {month_label(continuous_from)} to {month_label(cms_max)} |")
            else:
                lines.append(f"| Continuous from | GAPS exist — no continuous run to end |")

            if gaps:
                gap_strs = [month_label(g) for g in gaps[:10]]
                if len(gaps) > 10:
                    gap_strs.append(f"... +{len(gaps)-10} more")
                lines.append(f"| Missing months | {', '.join(gap_strs)} |")

            lines.append("")

        # Accounts with no data at all
        if not d['rbcm_months'] and not d['rbca_years'] and not d['cms_months']:
            lines.append(f"**No data in any source.**\n")

    # IB accounts note
    if ib_accounts:
        lines.append("---\n")
        lines.append("## Non-RBC (IB) Accounts — Not Analyzed\n")
        lines.append("These accounts will be reconciled separately.\n")
        lines.append("")
        seen = set()
        for ac_id, ac_name, cur in ib_accounts:
            label = f"{ac_id} ({ac_name})"
            if label not in seen:
                seen.add(label)
                lines.append(f"- {label}")
        lines.append("")

    # Overall summary
    lines.append("---\n")
    lines.append("## Overall Summary\n")
    lines.append("")

    if latest_month:
        lines.append(f"**Latest month across all RBC data:** {month_label(latest_month)}\n")
        lines.append("")

    # Per-account summary table
    lines.append(f"{'Account':>12} | {'Currency':>8} | {'RbcM months':>12} | {'RbcM true gaps':>14} | {'RbcM no-act skips':>17} | {'RbcM cont from':>15} | {'RbcA years':>10} | {'CMS months':>10} | {'CMS cont from':>14}")
    lines.append(f"{'-'*12}-|{'-'*8}-|{'-'*12}-|{'-'*14}-|{'-'*17}-|{'-'*15}-|{'-'*10}-|{'-'*10}-|{'-'*14}")

    for (ac_pk, cur), d in sorted(data.items()):
        rbcm_cont = "N/A"
        rbcm_true_gaps = 0
        rbcm_no_act = 0
        if d['rbcm_months']:
            rbcm_sorted = sorted(d['rbcm_months'])
            sorted_recs = [(mk, d['rbcm_records'][mk]) for mk in rbcm_sorted]
            tg, nas = classify_rbc_monthly_gaps(sorted_recs, d['rbcm_months'])
            rbcm_true_gaps = len(tg)
            rbcm_no_act = len(nas)
            for ym in rbcm_sorted:
                if not find_gaps_raw(d['rbcm_months'], ym, rbcm_sorted[-1]):
                    rbcm_cont = month_label(ym)
                    break

        cms_cont = "N/A"
        if d['cms_months']:
            cms_sorted = sorted(d['cms_months'])
            for ym in cms_sorted:
                if not find_gaps_raw(d['cms_months'], ym, cms_sorted[-1]):
                    cms_cont = month_label(ym)
                    break

        lines.append(
            f"{d['account_id']:>12} | {cur:>8} | {d['rbcm_count']:>12} | {rbcm_true_gaps:>14} | {rbcm_no_act:>17} | {rbcm_cont:>15} | {d['rbca_count']:>10} | {d['cms_count']:>10} | {cms_cont:>14}"
        )

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report 4: Proposal
# ---------------------------------------------------------------------------

def generate_proposal(data, ib_accounts):
    """Generate actionable proposal to fix identified gaps."""
    from datetime import datetime
    lines = []
    lines.append(f"# Report 4: Proposal to Fix Data Gaps\n")
    lines.append(f"**Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n")
    lines.append("Based on the reconciliation reports above, here are the identified issues and proposed fixes.\n")
    lines.append("**Scope:** RBC accounts only. IB accounts are out of scope.\n")
    lines.append("**Gap definition:** A 'true gap' in RbcMonthly means the balance changed between\n")
    lines.append("surrounding records — the PDF likely exists but wasn't imported. 'No-activity skips'\n")
    lines.append("are months where RBC didn't issue a statement because nothing changed.\n")
    lines.append("---\n")

    # Analyze issues
    issues = []

    for (ac_pk, cur), d in sorted(data.items()):
        ac_id = d['account_id']

        # Issue 1: RbcMonthly months where RbcAnnual is missing
        if d['rbcm_months']:
            rbcm_dec_years = {mk[0] for mk in d['rbcm_months'] if mk[1] == 12}
            missing_annual = rbcm_dec_years - d['rbca_years']
            if missing_annual:
                issues.append({
                    'type': 'missing_annual',
                    'account': ac_id,
                    'currency': cur,
                    'details': sorted(missing_annual),
                    'severity': 'HIGH' if len(missing_annual) > 3 else 'MEDIUM',
                })

        # Issue 2: RbcAnnual years where no December RbcMonthly exists
        if d['rbca_years']:
            rbcm_dec_years = {mk[0] for mk in d['rbcm_months'] if mk[1] == 12}
            missing_monthly = d['rbca_years'] - rbcm_dec_years
            if missing_monthly:
                issues.append({
                    'type': 'missing_dec_monthly',
                    'account': ac_id,
                    'currency': cur,
                    'details': sorted(missing_monthly),
                    'severity': 'MEDIUM',
                })

        # Issue 3: True gaps in RbcMonthly (balance changed, PDF likely exists)
        if d['rbcm_months']:
            rbcm_sorted = sorted(d['rbcm_months'])
            sorted_recs = [(mk, d['rbcm_records'][mk]) for mk in rbcm_sorted]
            true_gaps, no_activity_skips = classify_rbc_monthly_gaps(sorted_recs, d['rbcm_months'])

            if true_gaps:
                issues.append({
                    'type': 'rbcm_true_gaps',
                    'account': ac_id,
                    'currency': cur,
                    'details': true_gaps,
                    'count': len(true_gaps),
                    'severity': 'HIGH' if len(true_gaps) > 5 else 'MEDIUM',
                })

            if no_activity_skips:
                issues.append({
                    'type': 'rbcm_no_activity',
                    'account': ac_id,
                    'currency': cur,
                    'details': no_activity_skips,
                    'count': len(no_activity_skips),
                    'severity': 'LOW',
                })

        # Issue 4: Gaps in CMS
        if d['cms_months']:
            cms_sorted = sorted(d['cms_months'])
            gaps = find_gaps_raw(d['cms_months'], cms_sorted[0], cms_sorted[-1])
            if gaps:
                issues.append({
                    'type': 'cms_gaps',
                    'account': ac_id,
                    'currency': cur,
                    'details': gaps,
                    'count': len(gaps),
                    'severity': 'MEDIUM',
                })

        # Issue 5: No data at all
        if not d['rbcm_months'] and not d['rbca_years'] and not d['cms_months']:
            issues.append({
                'type': 'no_data',
                'account': ac_id,
                'currency': cur,
                'details': [],
                'severity': 'LOW',
            })

        # Issue 6: CMS exists but no RbcMonthly (shouldn't happen for RBC accounts)
        if d['cms_months'] and not d['rbcm_months']:
            issues.append({
                'type': 'cms_only',
                'account': ac_id,
                'currency': cur,
                'details': list(d['cms_months']),
                'count': len(d['cms_months']),
                'severity': 'LOW',
            })

    # Group by severity
    high = [i for i in issues if i['severity'] == 'HIGH']
    medium = [i for i in issues if i['severity'] == 'MEDIUM']
    low = [i for i in issues if i['severity'] == 'LOW']

    lines.append("## Issue Summary\n")
    lines.append(f"| Severity | Count |")
    lines.append(f"|----------|-------|")
    lines.append(f"| HIGH | {len(high)} |")
    lines.append(f"| MEDIUM | {len(medium)} |")
    lines.append(f"| LOW | {len(low)} |")
    lines.append("")

    # HIGH issues
    if high:
        lines.append("## HIGH Severity Issues\n")
        for issue in high:
            lines.append(f"### {issue['account']} / {issue['currency']} — {issue['type']}\n")
            if issue['type'] == 'missing_annual':
                years = ', '.join(map(str, issue['details']))
                lines.append(f"**Problem:** RbcAnnual records missing for years: {years}")
                lines.append(f"**Fix:** Import RBC Annual Report PDFs for these years from the processed/ folder or request from RBC.")
                lines.append(f"**Source:** December RbcMonthly exists, so annual report PDFs should be available.\n")
            elif issue['type'] == 'rbcm_true_gaps':
                gap_list = ', '.join([month_label(g) for g in issue['details'][:8]])
                if len(issue['details']) > 8:
                    gap_list += f" ... +{len(issue['details'])-8} more"
                lines.append(f"**Problem:** {issue['count']} true gaps in RbcMonthly — balance changed but PDF not imported: {gap_list}")
                lines.append(f"**Fix:** Locate and import RBC monthly statement PDFs for these months. These represent actual activity that was reported by RBC but the PDF wasn't imported. Check `processed/` folder for already-imported files that may have failed.\n")
        lines.append("")

    # MEDIUM issues
    if medium:
        lines.append("## MEDIUM Severity Issues\n")
        for issue in medium:
            lines.append(f"### {issue['account']} / {issue['currency']} — {issue['type']}\n")
            if issue['type'] == 'missing_dec_monthly':
                years = ', '.join(map(str, issue['details']))
                lines.append(f"**Problem:** RbcMonthly December records missing for years: {years}")
                lines.append(f"**Fix:** Import RBC monthly statement PDFs for December of these years. These are critical for reconciling against RbcAnnual.\n")
            elif issue['type'] == 'rbcm_true_gaps':
                gap_list = ', '.join([month_label(g) for g in issue['details'][:6]])
                if len(issue['details']) > 6:
                    gap_list += f" ... +{len(issue['details'])-6} more"
                lines.append(f"**Problem:** {issue['count']} true gaps in RbcMonthly — balance changed but PDF not imported: {gap_list}")
                lines.append(f"**Fix:** Locate and import RBC monthly statement PDFs for these months.\n")
            elif issue['type'] == 'cms_gaps':
                gap_list = ', '.join([month_label(g) for g in issue['details'][:6]])
                if len(issue['details']) > 6:
                    gap_list += f" ... +{len(issue['details'])-6} more"
                lines.append(f"**Problem:** {issue['count']} gaps in CoreMonthlyStatement (CSV): {gap_list}")
                lines.append(f"**Fix:** Import missing CSV files. These gaps may affect statement generation and reconciliation.\n")
            elif issue['type'] == 'missing_annual':
                years = ', '.join(map(str, issue['details']))
                lines.append(f"**Problem:** RbcAnnual records missing for years: {years}")
                lines.append(f"**Fix:** Import RBC Annual Report PDFs for these years.\n")
        lines.append("")

    # LOW issues
    if low:
        lines.append("## LOW Severity Issues\n")
        for issue in low:
            lines.append(f"### {issue['account']} / {issue['currency']} — {issue['type']}\n")
            if issue['type'] == 'no_data':
                lines.append(f"**Problem:** No data in any source (RbcMonthly, RbcAnnual, or CoreMonthlyStatement).")
                lines.append(f"**Fix:** This RBC account/currency combination may be new or inactive. Verify if data needs to be imported.\n")
            elif issue['type'] == 'cms_only':
                lines.append(f"**Problem:** Only CSV-derived CoreMonthlyStatement exists ({issue['count']} months). No PDF import data for this RBC account.")
                lines.append(f"**Fix:** Import RBC monthly statement PDFs if available. This is unusual for an RBC account.\n")
            elif issue['type'] == 'rbcm_no_activity':
                skip_list = ', '.join([month_label(s) for s in issue['details'][:5]])
                if len(issue['details']) > 5:
                    skip_list += f" ... +{len(issue['details'])-5} more"
                lines.append(f"**Problem:** {issue['count']} no-activity skips in RbcMonthly — RBC didn't issue statements: {skip_list}")
                lines.append(f"**Analysis:** These are expected. RBC only sends statements when there's activity. The balance was unchanged between surrounding statements.\n")
        lines.append("")

    # IB accounts note
    if ib_accounts:
        lines.append("---\n")
        lines.append("## IB Accounts (Out of Scope)\n")
        lines.append("These accounts will be reconciled in a future pass.\n")
        lines.append("")
        seen = set()
        for ac_id, ac_name, cur in ib_accounts:
            label = f"{ac_id} ({ac_name})"
            if label not in seen:
                seen.add(label)
                lines.append(f"- {label}: {cur}")
        lines.append("")

    # Proposed action plan
    lines.append("---\n")
    lines.append("## Proposed Action Plan\n")
    lines.append("")
    lines.append("### Phase 1: Quick Wins\n")
    lines.append("1. **Reconcile RbcMonthly vs RbcAnnual mismatches** — Investigate any balance differences > $1.00. Most are likely due to different valuation methodologies (market value vs adjusted value).\n")
    lines.append("2. **Import missing RbcAnnual records** — For accounts that have December RbcMonthly but no RbcAnnual, the annual report PDFs exist in the `processed/` folder. Re-import them.\n")
    lines.append("")
    lines.append("### Phase 2: Fill True Gaps\n")
    lines.append("3. **Import missing RbcMonthly PDFs (true gaps only)** — Focus on months where the balance actually changed. These represent real activity that was reported by RBC.\n")
    lines.append("4. **Ignore no-activity skips** — These are expected behavior. RBC doesn't issue statements when nothing changes.\n")
    lines.append("")
    lines.append("### Phase 3: Long-term\n")
    lines.append("5. **Standardize the import pipeline** — Ensure all future PDF imports go through the unified `import_inbox` command which handles both monthly and annual reports.\n")
    lines.append("6. **Set up automated reconciliation** — Create a management command that runs reconciliation checks monthly and flags new gaps.\n")
    lines.append("7. **IB account reconciliation** — Run a separate reconciliation pass for non-RBC accounts.\n")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Gathering data...")
    data, ib_accounts = gather_account_data()

    output_dir = Path(__file__).parent.parent / "docs" / "reconciliation"
    output_dir.mkdir(parents=True, exist_ok=True)

    reports = [
        ("recon_report_1_rbc_monthly_vs_annual.md", generate_rbc_monthly_vs_annual(data)),
        ("recon_report_2_rbc_monthly_vs_csv.md", generate_rbc_monthly_vs_csv(data)),
        ("recon_report_3_data_continuity.md", generate_continuity_report(data, ib_accounts)),
        ("recon_report_4_proposal.md", generate_proposal(data, ib_accounts)),
    ]

    for filename, content in reports:
        path = output_dir / filename
        path.write_text(content, encoding='utf-8')
        print(f"  Written: {path}")

    print(f"\nAnalyzed {len(data)} RBC account-currency pairs")
    print(f"Skipped {len(ib_accounts)} IB account-currency pairs")
    print("\nDone. Review the reports in docs/reconciliation/")


if __name__ == "__main__":
    main()
