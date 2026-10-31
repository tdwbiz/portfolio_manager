"""Read-only report helpers for the ``taxable_events`` command (step 15a).

Gathers the facts needed for the "known gaps in the taxable RBC accounts"
(docs/architecture.md §12, step 15): non-plain transactions, RBF interest,
DLR currency round-trips, cross-account symbols, book-cost anomalies.

Strictly read-only: only ``.filter()`` / ``.order_by()`` queries; the only
file written is the command's ``--out`` report. The report prints only
account names, dates, symbols, currencies, quantities and amounts — never
account digits; a file name, when printed at all, goes through
``classify_safe_name`` so only its safe kind is shown.

Plan: docs/plans/taxable-events.md (Decisions: tdw, Oct 31).
"""

import calendar
import re
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Q

from core.models import (
    Account,
    MonthlyCheckResult,
    RbcMonthly,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
    Transaction,
)
from core.utils.monthly_check import (
    classify_safe_name,
    effective_tx_date,
    make_symbol_key,
    normalize_option_text,
    option_text_from_notes,
    period_transactions,
)

# Section 1: the "plain" transaction types; anything else is reported.
PLAIN_TYPES = {"BUY", "SELL", "DIV", "DIST", "INT"}

# Section 2: January statements of these years carry the RBF reference rows.
RBF_REFERENCE_YEARS = (2024, 2025, 2026)

# Section 6/7 fixed targets (decision 2): report targets, not configuration.
SECTION6_ACCOUNT = "TS_HUISGELD"
SECTION6_MONTH = "2017-12"
SECTION7_ACCOUNT = "TS_CAR_LIVING"

# Section 9 fixed targets: stored verdicts to re-print.
SECTION9_ACCOUNT = "TS_CAR_LIVING"
SECTION9_MONTHS = ("2021-06", "2024-05")

# Section 3: a rate-looking token in a USD transaction's notes.
FX_NOTES_RE = re.compile(r"@\s*\d+\.\d+|FX")


def taxable_accounts(only_name=None):
    """TAXABLE accounts with at least one Transaction, ordered by name.

    ``only_name`` restricts to one exact account name; the result may be an
    empty list (the command turns that into a CommandError).
    """
    qs = Account.objects.filter(category="TAXABLE").order_by("name")
    if only_name:
        qs = qs.filter(name=only_name)
    return [account for account in qs if account.transactions.count() > 0]


def parse_month(text, option):
    """'YYYY-MM' -> (first day of month, last day of month)."""
    try:
        parsed = datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise ValueError(f"{option} must be YYYY-MM, got {text!r}") from None
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    return parsed, date(parsed.year, parsed.month, last_day)


def month_bounds(date_from, date_to):
    """(start, end) bounding dates for --from/--to; either may be None."""
    start = parse_month(date_from, "--from")[0] if date_from else None
    end = parse_month(date_to, "--to")[1] if date_to else None
    return start, end


def _in_range(day, month_start, month_end):
    """True when ``day`` sits inside the (open-ended) month bounds."""
    return (month_start is None or day >= month_start) and (
        month_end is None or day <= month_end
    )


# ----------------------------------------------------------------------
# Section functions — each returns a list of report lines.
# ----------------------------------------------------------------------

def section_1_non_plain(accounts, month_start, month_end):
    """Section 1: non-plain transactions in range (+ qty/amount and option catches)."""
    return []


def section_2_rbf_interest(accounts, month_start, month_end):
    """Section 2: RBF interest rows (CSV) + January PDF reference rows."""
    return []


def section_3_currencies(accounts, month_start, month_end):
    """Section 3: transaction counts per (currency, type); USD FX presence."""
    return []


def section_4_dlr(accounts, month_start, month_end):
    """Section 4: DLR rows from CSV transactions and PDF activity rows."""
    return []


def section_5_cross_account_symbols(accounts, month_start, month_end):
    """Section 5: symbols held in >= 2 accounts in the same statement month."""
    return []


def section_6_target_statement(account_name=SECTION6_ACCOUNT, month=SECTION6_MONTH):
    """Section 6: holdings of the fixed-target statement (missing book cost flagged)."""
    return []


def section_7_first_statement(account_name=SECTION7_ACCOUNT):
    """Section 7: holdings of the first stored statement of the target account."""
    return []


def section_8_book_cost_changes(accounts, month_start, month_end):
    """Section 8: PDF book-cost changes with no quantity change and no transaction."""
    return []


def section_9_stored_verdicts(account_name=SECTION9_ACCOUNT, months=SECTION9_MONTHS):
    """Section 9: stored MonthlyCheckResult rows for the fixed target months."""
    return []
