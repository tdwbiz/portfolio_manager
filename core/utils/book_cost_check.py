"""Read-only helpers for the ``book_cost_check`` command (step 15d).

Replays each taxable account's CSV transactions RBC-style — quantity and
book cost per (symbol key, currency), native currency, no FX, no pooling
across accounts — and compares the replayed state with the book costs
printed on the stored RBC monthly PDFs. It finds where the CSV ledger and
RBC's book cost disagree and where the difference arose: facts needed
before any ACB work (architecture §12, step 15).

Strictly read-only: only ``.filter()`` / ``.order_by()`` queries; nothing
is written to the database (the command writes only its ``--out`` file).
The report prints only account names, dates, symbols, currencies,
quantities and amounts — never account digits.

Plan: docs/plans/book-cost-check.md (Decisions: tdw, Oct 31).
"""

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal

from core.models import Account, RbcMonthly, RbcMonthlyHolding, Transaction
from core.utils.monthly_check import (
    effective_tx_date,
    make_symbol_key,
    normalize_option_text,
)
from core.utils.taxable_events import section_8_book_cost_changes

# Book cost is compared to the cent; PDF quantities are stored at 4 dp.
AMOUNT_TOL = Decimal("0.005")
QTY_TOL = Decimal("0.0001")

# Decision 1: one case-insensitive regex accepting both spellings
# ('REINV@C$10.00' and 'REINVEST @ $10.00'). The note price is used only
# when the row amount is 0 — a negative amount always wins.
REINV_RE = re.compile(r"REINV(?:EST)?\s*@\s*C?\$?\s*(\d+\.\d+)", re.IGNORECASE)
# RTC / notional adjustment amounts may start with a dot ('$.59').
RTC_COST_RE = re.compile(
    r"ADJUSTMENT TO BOOK COST \$(\.?\d+(?:\.\d+)?)", re.IGNORECASE
)
ADJ_VALUE_RE = re.compile(
    r"NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK VALUE \$(\.?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass
class SecurityState:
    """Replayed quantity and book cost for one (symbol key, currency).

    ``seed_date`` comes from the account's OpeningPosition (None = no start
    bound); only rows with an effective date strictly after it are replayed.
    ``not_checked_from`` is set by a REORGANIZATION row: comparisons on or
    after that date report NOT CHECKED (the security is still replayed).
    """

    quantity: Decimal = field(default_factory=lambda: Decimal("0"))
    cost: Decimal = field(default_factory=lambda: Decimal("0"))
    seed_date: object = None  # datetime.date | None
    not_checked_from: object = None  # datetime.date | None


@dataclass
class ReplayResult:
    """Full replay of one account.

    ``snapshots`` maps each stored statement date to the state after all
    rows with an effective date on or before that date (only non-zero
    states). ``tx_history`` maps (symbol key, currency) to the replayed
    rows in order as (effective_date, tx) pairs.
    """

    account_name: str
    notes: list = field(default_factory=list)  # seed notes (MANUAL rows)
    flags: list = field(default_factory=list)  # flagged transaction lines
    snapshots: dict = field(default_factory=dict)
    tx_history: dict = field(default_factory=dict)


def taxable_accounts_with_statements(only_name=None):
    """TAXABLE accounts with at least one stored RbcMonthly statement.

    Ordered by name; ``only_name`` restricts to one exact account name (the
    result may be an empty list — the command turns that into a
    CommandError, same style as ``taxable_events``).
    """
    qs = Account.objects.filter(
        category="TAXABLE", rbc_monthly_statements__isnull=False
    ).distinct().order_by("name")
    if only_name:
        qs = qs.filter(name=only_name)
    return list(qs)


def ordered_transactions(account_pk):
    """The account's transactions in replay order.

    Same placement as monthly_check: effective_tx_date, then tx.date, then
    tx.pk.
    """
    rows = Transaction.objects.filter(account_id=account_pk).order_by("date", "pk")
    return sorted(rows, key=lambda tx: (effective_tx_date(tx), tx.date, tx.pk))


def build_seeds(account):
    """Opening states per (symbol key, currency) + seed note lines.

    Each OpeningPosition seeds its security at its statement date; a row
    with ``statement=None`` (MANUAL) is seeded at the account's first stored
    statement date and a note is printed (decision 3). Accounts without
    opening positions start at zero everywhere with no start bound.
    """
    norm = make_symbol_key(account.pk)
    first_date = (
        RbcMonthly.objects.filter(account=account)
        .order_by("statement_date", "pk")
        .values_list("statement_date", flat=True)
        .first()
    )
    states = {}
    notes = []
    for op in account.opening_positions.select_related("statement").all():
        key = norm(op.symbol)
        if op.statement_id is None:
            seed_date = first_date
            notes.append(
                f"MANUAL opening position {key} [{op.currency}] has no "
                f"statement — seeded at first stored statement {first_date}"
            )
        else:
            seed_date = op.statement.statement_date
        state = states.setdefault((key, op.currency), SecurityState(seed_date=seed_date))
        state.quantity += op.quantity
        state.cost += op.book_cost
    return states, notes


def _remove_average(state, units):
    """Remove ``units`` at the current average cost (snap cost to 0 when the
    quantity reaches 0)."""
    if state.quantity > 0:
        state.cost -= state.cost / state.quantity * units
    state.quantity -= units
    if state.quantity == 0:
        state.cost = Decimal("0")


def apply_row(state, tx, eff):
    """Apply one replayed row to ``state``; return the flag reasons (list).

    Per-row rule order: RTC / notional notes rules first (they apply
    whatever the quantity), then REORGANIZATION marking, then
    TRANSFER-with-quantity, then the plain quantity rules. Always
    ``Transaction.amount`` (native currency) — never ``amount_cad`` (a USD
    row's amount_cad is CAD at the export day's rate).
    """
    notes = tx.notes or ""
    qty = tx.quantity
    amount = tx.amount
    reasons = []

    if tx.type == "RETURN_OF_CAPITAL" or (
        notes.startswith("RTC -") and "ADJUSTMENT TO BOOK COST" in notes
    ):
        match = RTC_COST_RE.search(notes)
        if match:
            state.cost -= Decimal(match.group(1))
        elif amount != 0:
            # Decision 2: no $X in the notes — fall back to the row amount.
            state.cost -= abs(amount)
        else:
            reasons.append(
                "RETURN_OF_CAPITAL with no $X in notes and zero amount"
            )

    if notes.startswith("ADJ -") and (
        "NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK VALUE" in notes
    ):
        match = ADJ_VALUE_RE.search(notes)
        if match:
            state.cost += Decimal(match.group(1))

    if tx.type == "REORGANIZATION":
        state.not_checked_from = eff
        reasons.append(f"REORGANIZATION — not checked from {eff}")

    if tx.type == "TRANSFER" and qty != 0:
        # Decision 6: both directions of the pair are flagged.
        if qty < 0:
            _remove_average(state, -qty)
        else:
            state.quantity += qty  # in-row adds 0 cost
        reasons.append("TRANSFER with quantity (receiving side loses basis)")
    elif qty > 0:
        state.quantity += qty
        if amount < 0:
            # A negative amount always wins over any note price (decision 1).
            state.cost += -amount
        elif amount == 0:
            match = REINV_RE.search(notes)
            if match:
                state.cost += qty * Decimal(match.group(1))
            else:
                reasons.append("qty>0 with amount 0 and no reinvest-at-price note")
    elif qty < 0:
        _remove_average(state, -qty)

    return reasons


def replay_account(account):
    """Replay one account's transactions RBC-style.

    State per (symbol key, currency); rows ordered by effective_tx_date,
    tx.date, tx.pk; only rows with an effective date strictly after the
    security's seed date are replayed. Snapshots are taken at every stored
    statement date of the account.
    """
    states, notes = build_seeds(account)
    norm = make_symbol_key(account.pk)
    txs = ordered_transactions(account.pk)
    stmt_dates = sorted(
        set(
            RbcMonthly.objects.filter(account=account).values_list(
                "statement_date", flat=True
            )
        )
    )

    result = ReplayResult(account_name=account.name, notes=notes)
    index = 0

    def apply(tx):
        eff = effective_tx_date(tx)
        key = norm((tx.symbol or "").strip() or "(no symbol)")
        currency = tx.currency
        state = states.get((key, currency))
        if state is None:
            state = SecurityState()
            states[(key, currency)] = state
        if state.seed_date is not None and eff <= state.seed_date:
            return  # only strictly after the seed date
        for reason in apply_row(state, tx, eff):
            result.flags.append(
                f"  FLAG {tx.date} {tx.type} {key} [{currency}] "
                f"qty={tx.quantity} amount={tx.amount} — {reason}"
            )
        result.tx_history.setdefault((key, currency), []).append((eff, tx))

    for day in stmt_dates:
        while index < len(txs) and effective_tx_date(txs[index]) <= day:
            apply(txs[index])
            index += 1
        result.snapshots[day] = {
            key_cur: replace(state)
            for key_cur, state in states.items()
            if state.quantity != 0 or state.cost != 0
        }
    # Rows after the last statement date are still replayed (no snapshot).
    while index < len(txs):
        apply(txs[index])
        index += 1
    return result


# ----------------------------------------------------------------------
# Comparison with the stored PDF book costs, summary and candidates.
# ----------------------------------------------------------------------


def _money(value):
    """Display a computed Decimal at cent precision."""
    return format(Decimal(value).quantize(Decimal("0.01")), "f")


def _qty(value):
    """Display a computed Decimal at the PDF's 4-dp quantity precision."""
    return format(Decimal(value).quantize(Decimal("0.0001")), "f")


def _pdf_holding_key(holding, norm):
    """Comparison key for a PDF holding (blank symbol -> normalised option
    text or security name, as in monthly_check)."""
    sym = (holding.symbol or "").strip()
    if not sym:
        sym = normalize_option_text(holding.security) or f"(no symbol: {holding.security})"
    return norm(sym)


@dataclass
class SecuritySummary:
    """Per (account, security, currency) comparison summary."""

    match_count: int = 0
    diff_count: int = 0
    first_diff: object = None  # datetime.date | None
    max_abs_cost_diff: Decimal = field(default_factory=lambda: Decimal("0"))


def compare_account(account, replay, month_start=None, month_end=None):
    """Compare the replay snapshots with the stored PDF book costs.

    Statements are grouped per statement_date (a date may carry CAD and USD
    RbcMonthly rows; holdings keep their own currency). ``month_start`` /
    ``month_end`` bound which statement dates are compared (decision 5) —
    the replay itself always starts from the seed. Returns (lines,
    summaries): one line per (statement date, PDF holding) plus one-sided
    entries; summaries maps (symbol key, currency) -> SecuritySummary.
    """
    norm = make_symbol_key(account.pk)
    pdf_by_date = {}
    holdings = (
        RbcMonthlyHolding.objects.filter(rbc_monthly__account=account)
        .select_related("rbc_monthly")
        .order_by("rbc_monthly__statement_date", "symbol", "security")
    )
    for h in holdings:
        day = h.rbc_monthly.statement_date
        if month_start is not None and day < month_start:
            continue
        if month_end is not None and day > month_end:
            continue
        pdf_by_date.setdefault(day, []).append(h)

    lines = []
    summaries = {}
    last_diff = {}  # (key, currency) -> (statement_date, cost_diff)
    last_match = {}  # (key, currency) -> statement_date
    for day in sorted(pdf_by_date):
        snapshot = replay.snapshots.get(day, {})
        seen_pdf = set()
        for h in pdf_by_date[day]:
            if h.book_cost == 0:
                continue  # means "not printed on the PDF"
            key = _pdf_holding_key(h, norm)
            currency = h.currency
            seen_pdf.add((key, currency))
            state = snapshot.get((key, currency))
            if (
                state is not None
                and state.not_checked_from is not None
                and state.not_checked_from <= day
            ):
                lines.append(
                    f"  {day} [{currency}] {key}: NOT CHECKED "
                    f"(reorganization on or before {state.not_checked_from})"
                )
                continue
            pdf_qty = h.quantity
            pdf_cost = h.book_cost
            if state is None:
                lines.append(
                    f"  {day} [{currency}] {key}: PDF-ONLY "
                    f"qty={_qty(pdf_qty)} book_cost={_money(pdf_cost)}"
                )
                continue
            cost_diff = state.cost - pdf_cost
            qty_diff = state.quantity - pdf_qty
            summary = summaries.setdefault((key, currency), SecuritySummary())
            if abs(cost_diff) <= AMOUNT_TOL and abs(qty_diff) <= QTY_TOL:
                # Decision 4: MATCH requires both cost and quantity.
                lines.append(
                    f"  {day} [{currency}] {key}: MATCH "
                    f"qty={_qty(state.quantity)} cost={_money(state.cost)}"
                )
                summary.match_count += 1
                last_match[(key, currency)] = day
            else:
                sides = []
                if abs(cost_diff) > AMOUNT_TOL:
                    sides.append(
                        f"cost replay {_money(state.cost)} vs PDF "
                        f"{_money(pdf_cost)} (diff {_money(cost_diff)})"
                    )
                if abs(qty_diff) > QTY_TOL:
                    sides.append(
                        f"qty replay {_qty(state.quantity)} vs PDF "
                        f"{_qty(pdf_qty)} (diff {_qty(qty_diff)})"
                    )
                line = f"  {day} [{currency}] {key}: DIFF — " + "; ".join(sides)
                prev = last_diff.get((key, currency))
                if prev is not None:
                    line += f"; change since {prev[0]}: {_money(cost_diff - prev[1])}"
                lines.append(line)
                summary.diff_count += 1
                if summary.first_diff is None:
                    summary.first_diff = day
                if abs(cost_diff) > summary.max_abs_cost_diff:
                    summary.max_abs_cost_diff = abs(cost_diff)
                since = last_match.get((key, currency))
                for eff, tx in replay.tx_history.get((key, currency), []):
                    if since is not None and eff <= since:
                        continue
                    lines.append(
                        f"      since last MATCH: {tx.date} {tx.type} "
                        f"{key} [{currency}] qty={_qty(tx.quantity)} "
                        f"amount={_money(tx.amount)}"
                    )
            last_diff[(key, currency)] = (day, cost_diff)
        # Replay-only securities with a non-zero quantity at this date.
        for (key, currency), state in sorted(snapshot.items()):
            if (key, currency) in seen_pdf or state.quantity == 0:
                continue
            lines.append(
                f"  {day} [{currency}] {key}: REPLAY-ONLY "
                f"qty={_qty(state.quantity)} cost={_money(state.cost)}"
            )
    return lines, summaries


def summary_lines(account_name, summaries):
    """Summary section lines: counts, first DIFF, largest |difference|."""
    lines = []
    for (key, currency), s in sorted(summaries.items()):
        lines.append(
            f"  {account_name} {key} [{currency}]: MATCH={s.match_count} "
            f"DIFF={s.diff_count} first DIFF {s.first_diff or '(none)'} "
            f"max |diff| {_money(s.max_abs_cost_diff)}"
        )
    return lines


def candidates_lines(accounts, month_start=None, month_end=None):
    """Candidates section: unexplained PDF book-cost changes.

    Reuses ``section_8_book_cost_changes`` from taxable_events directly
    (imported, not copied).
    """
    return section_8_book_cost_changes(accounts, month_start, month_end)
