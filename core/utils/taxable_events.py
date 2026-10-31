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

# Section 1: known REORGANIZATION subtypes (first token of the notes).
REORG_SUBTYPES = {"DIS", "CIL", "MGR", "MER", "NAC", "EXP"}


def _reorg_subtype(notes):
    """First token of a REORGANIZATION's notes, '?' when unknown."""
    first = (notes or "").strip().split()
    if not first:
        return "?"
    token = first[0].upper()
    return token if token in REORG_SUBTYPES else "?"


def _option_marker(tx):
    """Option marker for a transaction row, else None.

    Short-option convention (symbol starts with '9'), option text in the
    notes, or an option-looking symbol — via the monthly_check helpers.
    """
    sym = (tx.symbol or "").strip()
    if sym.startswith("9"):
        return f"short-option code {sym}"
    text = option_text_from_notes(tx.notes)
    if text is not None:
        return f"option text in notes: {text}"
    normalised = normalize_option_text(sym)
    if normalised is not None:
        return f"option symbol: {normalised}"
    return None


def section_1_non_plain(accounts, month_start, month_end):
    """Section 1: every non-plain transaction in range.

    Plain = BUY/SELL/DIV/DIST/INT. Extra catches from any type: rows with
    quantity != 0 and amount_cad == 0, and rows carrying an option symbol.
    TRANSFER rows are listed only when quantity != 0 (the plain cash
    transfers are noise). REORGANIZATION rows carry their subtype (first
    token of the notes); RETURN_OF_CAPITAL rows print value, quantity and
    full notes. Period membership uses effective_tx_date.
    """
    lines = []
    for account in accounts:
        txs = Transaction.objects.filter(account=account).order_by("date", "pk")
        for tx in txs:
            if not _in_range(effective_tx_date(tx), month_start, month_end):
                continue
            if tx.type == "TRANSFER" and tx.quantity == 0:
                continue
            reasons = []
            if tx.type not in PLAIN_TYPES:
                reasons.append(f"type={tx.type}")
            if tx.quantity != 0 and tx.amount_cad == 0:
                reasons.append("qty!=0 with amount_cad=0")
            marker = _option_marker(tx)
            if marker is not None:
                reasons.append(marker)
            if not reasons:
                continue
            sub = (
                f"[{_reorg_subtype(tx.notes)}]"
                if tx.type == "REORGANIZATION"
                else ""
            )
            lines.append(
                f"  {account.name} {tx.date} {tx.type}{sub} "
                f"sym={tx.symbol or '(none)'} qty={tx.quantity} "
                f"price={tx.price} amount_cad={tx.amount_cad} cur={tx.currency}"
            )
            lines.append(f"      why: {'; '.join(reasons)}")
            if tx.notes:
                lines.append(f"      notes: {tx.notes}")
    return lines


def _is_rbf_reference_row(row):
    """True for an RBF-related January reference row (decision 1).

    Symbol starts with RBF, or activity_type INTEREST_RECEIVED, or the
    description contains INVESTMENT SAVINGS (PDF rows often carry only the
    security name, no symbol).
    """
    if (row.symbol or "").strip().upper().startswith("RBF"):
        return True
    if row.activity_type == "INTEREST_RECEIVED":
        return True
    return "INVESTMENT SAVINGS" in (row.description or "").upper()


def section_2_rbf_interest(accounts, month_start, month_end):
    """Section 2: RBF interest rows.

    CSV: every Transaction whose symbol starts with RBF in range (date,
    settlement date, quantity, price, amount, notes). PDF: per account, the
    reference rows of the January statements of RBF_REFERENCE_YEARS — rows
    matching _is_rbf_reference_row — with date, symbol, quantity, price,
    debit, credit, description.
    """
    lines = []
    for account in accounts:
        txs = (
            Transaction.objects.filter(account=account, symbol__startswith="RBF")
            .order_by("date", "pk")
        )
        for tx in txs:
            if not _in_range(effective_tx_date(tx), month_start, month_end):
                continue
            settle = tx.settlement_date or "(none)"
            line = (
                f"  {account.name} CSV {tx.date} settle={settle} "
                f"qty={tx.quantity} price={tx.price} amount_cad={tx.amount_cad}"
            )
            if tx.notes:
                line += f" — {tx.notes}"
            lines.append(line)
        for year in RBF_REFERENCE_YEARS:
            stmts = (
                RbcMonthly.objects.filter(
                    account=account,
                    statement_date__year=year,
                    statement_date__month=1,
                )
                .order_by("currency", "pk")
            )
            for stmt in stmts:
                rows = (
                    RbcMonthlyTransaction.objects.filter(rbc_monthly=stmt)
                    .order_by("date", "pk")
                )
                for row in rows:
                    if not _is_rbf_reference_row(row):
                        continue
                    if not _in_range(row.date, month_start, month_end):
                        continue
                    lines.append(
                        f"  {account.name} PDF Jan {year} [{stmt.currency}] "
                        f"{row.date} sym={row.symbol or '(none)'} "
                        f"qty={row.quantity} price={row.price} "
                        f"debit={row.debit} credit={row.credit}"
                    )
                    if row.description:
                        lines.append(f"      desc: {row.description}")
    return lines


def section_3_currencies(accounts, month_start, month_end):
    """Section 3: per account, transaction count per (currency, type).

    For USD rows, whether an exchange rate is carried — counted separately:
    the exchange_rate_cad field != 1, and a rate-looking token in the notes
    ('@<rate>' or 'FX').
    """
    lines = []
    for account in accounts:
        txs = Transaction.objects.filter(account=account).order_by("date", "pk")
        counts = {}
        field_fx = 0
        notes_fx = 0
        for tx in txs:
            if not _in_range(effective_tx_date(tx), month_start, month_end):
                continue
            key = (tx.currency, tx.type)
            counts[key] = counts.get(key, 0) + 1
            if tx.currency == "USD":
                if tx.exchange_rate_cad != 1:
                    field_fx += 1
                if FX_NOTES_RE.search(tx.notes or ""):
                    notes_fx += 1
        for (currency, ttype), count in sorted(counts.items()):
            lines.append(f"  {account.name} {currency} {ttype}: {count}")
        lines.append(
            f"  {account.name} USD rows with exchange_rate_cad != 1: {field_fx}"
        )
        lines.append(
            f"  {account.name} USD rows with rate-looking notes token "
            f"(@x.xx or FX): {notes_fx}"
        )
    return lines


def section_4_dlr(accounts, month_start, month_end):
    """Section 4: DLR currency round-trip rows.

    CSV: every Transaction with symbol DLR (notes printed); PDF: every
    RbcMonthlyTransaction with symbol DLR or DLR in the description
    (description printed). Dates and amounts only.
    """
    lines = []
    for account in accounts:
        txs = (
            Transaction.objects.filter(account=account, symbol="DLR")
            .order_by("date", "pk")
        )
        for tx in txs:
            if not _in_range(effective_tx_date(tx), month_start, month_end):
                continue
            line = (
                f"  {account.name} CSV {tx.date} {tx.type} qty={tx.quantity} "
                f"amount_cad={tx.amount_cad}"
            )
            if tx.notes:
                line += f" — notes: {tx.notes}"
            lines.append(line)
        rows = (
            RbcMonthlyTransaction.objects.filter(rbc_monthly__account=account)
            .filter(Q(symbol="DLR") | Q(description__icontains="DLR"))
            .order_by("date", "pk")
        )
        for row in rows:
            if not _in_range(row.date, month_start, month_end):
                continue
            line = (
                f"  {account.name} PDF {row.date} {row.activity_type} "
                f"debit={row.debit} credit={row.credit}"
            )
            if row.description:
                line += f" — desc: {row.description}"
            lines.append(line)
    return lines


def section_5_cross_account_symbols(accounts, month_start, month_end):
    """Section 5: symbols held in >= 2 accounts in the same statement month.

    RbcMonthlyHolding rows grouped by statement year-month and normalised
    symbol — each account's own make_symbol_key (option codes map to their
    normalised option text, aliases applied), so cross-account comparison
    happens under one key per account. Quantity and currency per account
    are printed.
    """
    per_month = {}
    for account in accounts:
        norm = make_symbol_key(account.pk)
        holdings = (
            RbcMonthlyHolding.objects.filter(rbc_monthly__account=account)
            .select_related("rbc_monthly")
        )
        for h in holdings:
            statement_date = h.rbc_monthly.statement_date
            if not _in_range(statement_date, month_start, month_end):
                continue
            sym = (h.symbol or "").strip()
            if not sym:
                sym = normalize_option_text(h.security) or f"(no symbol: {h.security})"
            key = norm(sym)
            per_account = (
                per_month.setdefault(
                    (statement_date.year, statement_date.month), {}
                )
                .setdefault(key, {})
            )
            entry = per_account.setdefault(
                account.name, {"qty": Decimal("0"), "currencies": set()}
            )
            entry["qty"] += h.quantity
            entry["currencies"].add(h.currency)
    lines = []
    for year, month in sorted(per_month):
        for key in sorted(per_month[(year, month)]):
            per_account = per_month[(year, month)][key]
            if len(per_account) < 2:
                continue
            parts = [
                f"{name} qty={entry['qty']} {'/'.join(sorted(entry['currencies']))}"
                for name, entry in sorted(per_account.items())
            ]
            lines.append(f"  {year}-{month:02d} {key}: " + "; ".join(parts))
    return lines


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
