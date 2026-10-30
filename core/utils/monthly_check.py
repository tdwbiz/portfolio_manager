"""Read-only helpers for the ``monthly_check`` command (phase 4, step 14a).

Compares CSV ``Transaction`` rows with the stored RBC monthly statements
(``RbcMonthly`` + balances/holdings/PDF transactions) and produces a verdict
per (account, statement_date, currency): FIRST / NO_CSV / PASS / FAIL, plus
an import-log section for errored PDFs.

Pure read-only: only ``.filter()`` / ``.aggregate()`` queries; nothing is
written to the database. The report contains only account names, dates,
currencies, symbols, amounts and file names — never account digits.

Plan: docs/plans/monthly-check-report.md (Decisions: tdw, Oct 30).
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Max, Min, Q, Sum

from core.models import (
    FileImportLog,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
    Transaction,
)

# Cash and chain amounts are compared to the cent.
AMOUNT_TOL = Decimal("0.005")
# PDF quantities are stored at 4 dp (decision 3).
QTY_TOL = Decimal("0.0001")

# Income rows are dated by their payment date when the notes carry one
# ('REC 12/21/21 PAY 12/31/21'): RBC books year-end dividends in the
# December statement while the CSV dates them Jan 2-4 (real-data fix 1).
INCOME_TYPES = {"DIV", "DIST", "INT"}
_PAY_DATE_RE = re.compile(r"PAY\s+(\d{2})/(\d{2})/(\d{2})")
# How far outside the period an income row's CSV date may sit while its
# payment date is inside: fetch this wide, decide membership in Python.
DATE_SLACK_DAYS = 15


@dataclass
class CurrencyResult:
    """Verdict for one (account, statement_date, currency).

    ``verdict`` is FIRST / NO_CSV / PASS / FAIL. ``failures`` holds one line
    per failed check (CASH / QUANTITY / CHAIN) with its details; it is empty
    unless verdict == 'FAIL'.
    """

    currency: str
    verdict: str = "PASS"
    failures: list = field(default_factory=list)


@dataclass
class StatementCheck:
    """Verdicts for one statement (all currencies) of one account."""

    account_name: str
    statement_date: object  # datetime.date
    currencies: list = field(default_factory=list)  # [CurrencyResult]
    symbol_pairs: list = field(default_factory=list)  # [(csv_sym|None, pdf_sym|None)]

    @property
    def failed_kinds(self):
        """Sorted unique failure kinds across all currencies (for summaries)."""
        kinds = set()
        for cur in self.currencies:
            for line in cur.failures:
                kinds.add(line.split(":", 1)[0].strip())
        return sorted(kinds)


@dataclass
class StatementGroup:
    """All RbcMonthly rows of one account with the same statement_date.

    ``rows`` maps currency -> the winning RbcMonthly row (newest ``parsed_at``
    when several rows share a currency, decision 4). ``duplicates`` maps
    currency -> every file_name seen for that currency (only present for
    currencies with more than one row); each is a finding in the report.
    """

    account_pk: int
    account_name: str
    statement_date: object  # datetime.date
    rows: dict = field(default_factory=dict)
    duplicates: dict = field(default_factory=dict)


def csv_coverage_bounds():
    """Global (first, last) settlement date over ALL Transactions.

    Decision 2 (tdw, Oct 30): coverage is global, not per account — one set
    of exports covers every account, so a quiet account must not be NO_CSV
    after its own last transaction. Rows with NULL settlement_date are not
    part of the bounds.
    """
    agg = Transaction.objects.exclude(settlement_date__isnull=True).aggregate(
        first=Min("settlement_date"), last=Max("settlement_date")
    )
    return agg["first"], agg["last"]


def group_statements(accounts):
    """Group RbcMonthly rows per (account, statement_date), sorted.

    ``accounts`` is an Account queryset. Newest ``parsed_at`` wins per
    (statement_date, currency); the losers are recorded in ``duplicates``.
    """
    rows = (
        RbcMonthly.objects.filter(account__in=accounts)
        .select_related("account")
        .order_by("statement_date", "currency", "parsed_at")
    )
    groups = {}
    for row in rows:
        key = (row.account_id, row.statement_date)
        group = groups.setdefault(
            key,
            StatementGroup(
                account_pk=row.account_id,
                account_name=row.account.name,
                statement_date=row.statement_date,
            ),
        )
        if row.currency in group.rows:
            group.duplicates.setdefault(row.currency, []).append(row.file_name)
        # Ordered by parsed_at ascending within (statement_date, currency),
        # so the last assignment is the newest row.
        group.rows[row.currency] = row
    return sorted(groups.values(), key=lambda g: (g.account_name, g.statement_date))


def previous_groups(groups):
    """Map each StatementGroup to its previous statement group (or None).

    Previous = the account's latest stored statement with a strictly earlier
    statement_date; a gap of several months is fine.
    """
    by_account = {}
    for group in groups:  # already sorted by (account_name, statement_date)
        by_account.setdefault(group.account_pk, []).append(group)
    prev_of = {}
    for account_groups in by_account.values():
        for i, group in enumerate(account_groups):
            prev_of[id(group)] = account_groups[i - 1] if i > 0 else None
    return prev_of


def check_chain(cur_row, prev_group):
    """CHAIN (per currency): returns failure lines (empty list = holds).

    ``begin_total_market_value`` must equal the previous statement's
    ``end_total_market_value`` for the same currency (±0.005) and
    ``last_statement_date`` must equal the previous ``statement_date``.
    """
    failures = []
    prev_row = prev_group.rows.get(cur_row.currency)
    if prev_row is None:
        failures.append(
            f"CHAIN: no previous {cur_row.currency} section on "
            f"{prev_group.statement_date} to chain from"
        )
        return failures
    diff = cur_row.begin_total_market_value - prev_row.end_total_market_value
    if abs(diff) > AMOUNT_TOL:
        failures.append(
            f"CHAIN: begin_total_market_value {cur_row.begin_total_market_value} "
            f"!= previous end_total_market_value {prev_row.end_total_market_value} "
            f"(diff {diff})"
        )
    if cur_row.last_statement_date != prev_row.statement_date:
        failures.append(
            f"CHAIN: last_statement_date {cur_row.last_statement_date} "
            f"!= previous statement_date {prev_row.statement_date}"
        )
    return failures


def _pay_date(notes):
    """Payment date from notes ('PAY mm/dd/yy', 2-digit year -> 20yy), else None."""
    match = _PAY_DATE_RE.search(notes or "")
    if not match:
        return None
    month, day, year = (int(match.group(i)) for i in (1, 2, 3))
    try:
        return date(year + 2000, month, day)
    except ValueError:
        return None


def effective_tx_date(tx):
    """The date that decides a transaction's statement period.

    Income rows (DIV / DIST / INT): the payment date from the notes when
    present, else settlement_date, else trade date. Everything else:
    settlement_date, else trade date (real-data fix 1).
    """
    if tx.type in INCOME_TYPES:
        pay = _pay_date(tx.notes)
        if pay is not None:
            return pay
    return tx.settlement_date or tx.date


def period_transactions(account_pk, currency, period_start, period_end):
    """Transactions whose effective date is in (period_start, period_end].

    Fetches a window widened by DATE_SLACK_DAYS on both sides (income rows
    may carry a CSV date outside the period while their payment date is
    inside) and filters in Python. ``currency`` None = all currencies of
    the account.
    """
    lo = period_start - timedelta(days=DATE_SLACK_DAYS)
    hi = period_end + timedelta(days=DATE_SLACK_DAYS)
    qs = Transaction.objects.filter(
        Q(date__gte=lo, date__lte=hi)
        | Q(settlement_date__gte=lo, settlement_date__lte=hi),
        account_id=account_pk,
    )
    if currency is not None:
        qs = qs.filter(currency=currency)
    return [
        tx
        for tx in qs.order_by("date", "pk")
        if period_start < effective_tx_date(tx) <= period_end
    ]


def _trade_date_q(period_start, period_end):
    """Same window but on the trade date (for the CASH replay diagnostic)."""
    return Q(date__gt=period_start, date__lte=period_end)


def _cash_closing(rbc_monthly_row, currency):
    """Closing balance of the Cash section for a currency (None if absent)."""
    bal = RbcMonthlyBalance.objects.filter(
        rbc_monthly=rbc_monthly_row, account_type="Cash", currency=currency
    ).first()
    return None if bal is None else bal.closing_balance


def align_cash_rows(csv_txs, pdf_txs):
    """Greedy one-to-one match of CSV cash rows against PDF activity rows.

    Match rule: equal net amount (±0.005) with ≤ 5 days date slack (CSV row's
    effective date vs the PDF row's date). Returns (unmatched_csv, unmatched_pdf)
    where pdf entries are RbcMonthlyTransaction rows and csv entries are
    Transaction rows. Diagnostics only — RbcMonthlyTransaction is unreliable
    and never used for the check itself.
    """
    unmatched_pdf = list(pdf_txs)
    unmatched_csv = []
    for tx in sorted(csv_txs, key=lambda t: (t.date, t.pk)):
        best = None
        for pdf in unmatched_pdf:
            net = pdf.credit - pdf.debit
            if abs(net - tx.amount) > AMOUNT_TOL:
                continue
            if abs((pdf.date - tx.date).days) > 5:
                continue
            if best is None or pdf.date < best.date:
                best = pdf
        if best is not None:
            unmatched_pdf.remove(best)
        else:
            unmatched_csv.append(tx)
    return unmatched_csv, unmatched_pdf


def check_cash(group, prev_group, currency):
    """CASH (per currency): returns failure lines (empty list = holds).

    start = previous statement's Cash closing_balance for the currency
    (0 if that currency had no Cash section) + Σ Transaction.amount of the
    period (signed, native currency — decision 1); must equal this
    statement's Cash closing_balance to the cent.
    """
    cur_row = group.rows[currency]
    prev_row = prev_group.rows.get(currency)
    start = Decimal("0.00")
    if prev_row is not None:
        start = _cash_closing(prev_row, currency) or Decimal("0.00")

    actual_bal = _cash_closing(cur_row, currency)
    period_start = prev_group.statement_date
    period_end = group.statement_date

    txs = period_transactions(group.account_pk, currency, period_start, period_end)
    flows = sum((tx.amount for tx in txs), Decimal("0.00"))
    expected = start + flows

    actual = Decimal("0.00") if actual_bal is None else actual_bal

    if abs(actual - expected) <= AMOUNT_TOL:
        return []

    lines = [
        f"CASH: expected {expected} (start {start} + flows {flows}), "
        f"got {actual}, diff {actual - expected}"
    ]
    if actual_bal is None:
        lines.append("CASH: this statement has no Cash section for this currency")

    pdf_txs = list(
        RbcMonthlyTransaction.objects.filter(
            rbc_monthly=cur_row, currency=currency
        ).order_by("date", "pk")
    )
    unmatched_csv, unmatched_pdf = align_cash_rows(txs, pdf_txs)
    for tx in unmatched_csv:
        lines.append(
            f"CASH: unmatched CSV row: {tx.date} {tx.type} {tx.symbol or '(no symbol)'} "
            f"{tx.amount}"
        )
    for pdf in unmatched_pdf:
        lines.append(
            f"CASH: unmatched PDF row: {pdf.date} {pdf.activity_type} "
            f"net {pdf.credit - pdf.debit} — {pdf.description[:60]}"
        )

    # Diagnostic: would the cash sum pass using trade date instead of
    # settlement date for period membership?
    replay_txs = Transaction.objects.filter(
        _trade_date_q(period_start, period_end),
        account_id=group.account_pk,
        currency=currency,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    replay_expected = start + replay_txs
    if abs(actual - replay_expected) <= AMOUNT_TOL:
        lines.append("CASH: trade-date replay would pass")
    else:
        lines.append(
            f"CASH: trade-date replay still fails (expected {replay_expected})"
        )
    return lines


# Known symbol aliases between CSV activity and PDF holdings (real-data
# fix 3), applied to both sides before comparing.
SYMBOL_ALIASES = {
    "FB": "META",
    "ETHX.U": "ETHXF",
    "EBIT.U": "BTEUF",
    "E012211": "ENF",
    "P031613": "SPHD",
    "P040422": "CQQQ",
    "C024526": "CQQQ",
}

# Option text as it appears in RBC PDF holdings and CSV transaction notes:
# CALL/PUT, underlying, mm/dd/yy expiry, strike (real-data fix 2). The CSV
# uses RBC security codes (9BSCCN2, 8UJDMB6) instead of this text.
_OPTION_TEXT_RE = re.compile(
    r"(?<![A-Z])(?P<side>CALL|PUT)\s*(?P<underlying>[A-Z0-9]+\.?)\s+"
    r"(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{2,4})\s+(?P<strike>\d+(?:\.\d+)?)"
)


def normalize_option_text(text):
    """Normalise option text for comparison (real-data fix 2).

    'CALLNVDA 07/30/21 210.0' -> 'CALL NVDA 07/30/21 210': a space after
    CALL/PUT, upper case, strike without trailing zeros. Returns None when
    the text is not option text.
    """
    match = _OPTION_TEXT_RE.match((text or "").strip().upper())
    if not match:
        return None
    strike = format(Decimal(match.group("strike")).normalize(), "f")
    return (
        f"{match.group('side')} {match.group('underlying')} "
        f"{match.group('m')}/{match.group('d')}/{match.group('y')} {strike}"
    )


def option_text_from_notes(notes):
    """Normalised option text found in a Transaction's notes, else None."""
    match = _OPTION_TEXT_RE.search((notes or "").upper())
    return normalize_option_text(match.group(0)) if match else None


def _option_code_map(account_pk):
    """CSV option code -> normalised option text (real-data fix 2).

    The option text is taken from that symbol's Transaction notes (e.g.
    'BUY CALL QQQ 03/31/21 321'); the first note carrying it wins.
    """
    out = {}
    txs = (
        Transaction.objects.filter(account_id=account_pk)
        .exclude(symbol="")
        .only("symbol", "notes")
    )
    for tx in txs:
        sym = tx.symbol.strip()
        if not sym or sym in out:
            continue
        text = option_text_from_notes(tx.notes)
        if text is not None:
            out[sym] = text
    return out


def _pdf_qty_map(rbc_rows):
    """Pooled quantity per symbol over the given RbcMonthly rows (all
    currencies — the DLR journal moves shares between the CAD and USD sides,
    so pooling is deliberate). Blank symbols are keyed by security name."""
    qtys = {}
    holdings = RbcMonthlyHolding.objects.filter(
        rbc_monthly__in=[row.pk for row in rbc_rows]
    )
    for h in holdings:
        key = (h.symbol or "").strip() or f"(no symbol: {h.security})"
        qtys[key] = qtys.get(key, Decimal("0")) + h.quantity
    return qtys


def check_holdings(group, prev_group):
    """HOLDINGS / QUANTITY (per account, currencies pooled).

    start = previous statement's holdings quantities per symbol + Σ signed
    Transaction.quantity in the period; must equal this statement's holdings
    per symbol at 0.0001 tolerance (decision 3). CSV option codes are mapped
    to their normalised option text from the transaction notes and PDF option
    text is normalised in place, and known symbol aliases (SYMBOL_ALIASES)
    are applied to both sides, so everything compares under one key
    (real-data fixes 2-3). Returns (failure_lines,
    symbol_pairs): pairs line up CSV-only and PDF-only symbols (sorted, by
    position) so a symbol map can be designed later; unmatched entries carry
    None on the missing side.
    """
    period_start = prev_group.statement_date
    period_end = group.statement_date

    option_map = _option_code_map(group.account_pk)

    def norm(sym):
        # CSV option codes map to their normalised option text (from the
        # transaction notes); PDF option text is normalised in place.
        s = sym.strip()
        if s in option_map:
            return option_map[s]
        normalised = normalize_option_text(s)
        if normalised is not None:
            return normalised
        return SYMBOL_ALIASES.get(s.upper(), s)

    prev_qtys = {}
    for sym, qty in _pdf_qty_map(list(prev_group.rows.values())).items():
        key = norm(sym)
        prev_qtys[key] = prev_qtys.get(key, Decimal("0")) + qty
    cur_qtys = {}
    for sym, qty in _pdf_qty_map(list(group.rows.values())).items():
        key = norm(sym)
        cur_qtys[key] = cur_qtys.get(key, Decimal("0")) + qty

    delta = {}
    for tx in period_transactions(
        group.account_pk, None, period_start, period_end
    ):
        raw = (tx.symbol or "").strip() or "(no symbol)"
        key = norm(raw)
        delta[key] = delta.get(key, Decimal("0")) + tx.quantity

    expected = {}
    for sym in set(prev_qtys) | set(delta):
        expected[sym] = prev_qtys.get(sym, Decimal("0")) + delta.get(sym, Decimal("0"))

    failures = []
    for sym in sorted(set(expected) | set(cur_qtys)):
        exp = expected.get(sym, Decimal("0"))
        act = cur_qtys.get(sym, Decimal("0"))
        if abs(exp - act) > QTY_TOL:
            failures.append(
                f"QUANTITY: {sym}: expected {exp} (prev "
                f"{prev_qtys.get(sym, Decimal('0'))} + csv "
                f"{delta.get(sym, Decimal('0'))}), got {act}"
            )

    # CSV-only / PDF-only symbols with a non-zero expected/actual quantity —
    # candidates for a symbol map (option codes etc.), collected per account.
    csv_only = sorted(
        sym for sym in set(expected) - set(cur_qtys)
        if abs(expected[sym]) > QTY_TOL
    )
    pdf_only = sorted(
        sym for sym in set(cur_qtys) - set(expected)
        if abs(cur_qtys[sym]) > QTY_TOL
    )
    pairs = list(zip(csv_only, pdf_only))
    pairs += [(sym, None) for sym in csv_only[len(pairs):]]
    pairs += [(None, sym) for sym in pdf_only[len(pairs):]]
    return failures, pairs


# Same pattern as core/utils/safe_names.py (kept local so this plan's file
# list stays exactly the three listed files). FileImportLog stores safe names.
_SAFE_NAME_KIND_RE = re.compile(
    r"^(?P<date>\d{4}(?:-\d{2}-\d{2})?)_"
    r"(?P<kind>monthly|annual|unknown)_"
    r"(?P<account>[A-Z0-9]+(?:_[A-Z0-9]+)*)_"
    r"(?P<hash>[0-9a-f]{8})\."
    r"(?P<ext>[A-Za-z0-9]+)$"
)


def classify_safe_name(filename):
    """'monthly' / 'annual' from the safe file name, else None (decision 5).

    ``unknown`` kinds and anything that is not a safe name at all return
    None — those go under "other errors" so nothing is silently dropped.
    """
    match = _SAFE_NAME_KIND_RE.match(filename or "")
    if not match:
        return None
    kind = match.group("kind")
    return kind if kind in ("monthly", "annual") else None


def scan_import_logs():
    """Latest FileImportLog row per filename, split for the IMPORT section.

    Only logs whose LATEST row has status='error' are listed. Split:
    - 'conflicts': error_message contains "conflict" (case-insensitive,
      decision 6) — second copies of corrected statements; kept out of the
      parked list, shown with status + reason.
    - 'parked': monthly safe names whose content_hash matches no RbcMonthly
      row (errored and never stored).
    - 'annual': annual safe names.
    - 'other': everything else (unknown kind, not a safe name, or a monthly
      error whose hash does match a stored statement).
    """
    latest = {}
    for log in FileImportLog.objects.order_by("imported_at", "pk"):
        latest[log.filename] = log  # last write per filename wins

    stored_hashes = set(
        RbcMonthly.objects.exclude(content_hash="").values_list(
            "content_hash", flat=True
        )
    )

    result = {"parked": [], "conflicts": [], "annual": [], "other": []}
    for log in sorted(latest.values(), key=lambda l: l.filename):
        if log.status != "error":
            continue
        entry = {
            "filename": log.filename,
            "status": log.status,
            "reason": log.error_message or "(no error message)",
            "imported_at": log.imported_at,
        }
        if "conflict" in (log.error_message or "").lower():
            result["conflicts"].append(entry)
            continue
        kind = classify_safe_name(log.filename)
        if kind == "monthly":
            if log.content_hash and log.content_hash in stored_hashes:
                entry["reason"] += " (hash matches a stored RbcMonthly row)"
                result["other"].append(entry)
            else:
                result["parked"].append(entry)
        elif kind == "annual":
            result["annual"].append(entry)
        else:
            result["other"].append(entry)
    return result


def check_statement(group, prev_group, bounds):
    """Build the StatementCheck for one group.

    FIRST (no previous statement) and NO_CSV (period outside the global CSV
    coverage) short-circuit; otherwise CHAIN and CASH run per currency and
    QUANTITY once per account (pooled, attached to every currency).
    """
    result = StatementCheck(
        account_name=group.account_name, statement_date=group.statement_date
    )
    if prev_group is None:
        for cur in sorted(group.rows):
            result.currencies.append(CurrencyResult(currency=cur, verdict="FIRST"))
        return result

    first_settle, last_settle = bounds
    period_start = prev_group.statement_date  # exclusive
    period_end = group.statement_date  # inclusive
    no_csv = (
        first_settle is None
        or period_end < first_settle
        or period_start >= last_settle
    )

    if no_csv:
        for cur in sorted(group.rows):
            result.currencies.append(CurrencyResult(currency=cur, verdict="NO_CSV"))
        return result

    # QUANTITY is per account (pooled): run once, attach to every currency.
    quantity_failures, pairs = check_holdings(group, prev_group)
    result.symbol_pairs = pairs
    for cur in sorted(group.rows):
        failures = check_chain(group.rows[cur], prev_group)
        failures.extend(check_cash(group, prev_group, cur))
        failures.extend(quantity_failures)
        result.currencies.append(
            CurrencyResult(
                currency=cur,
                verdict="FAIL" if failures else "PASS",
                failures=failures,
            )
        )
    return result
