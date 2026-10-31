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

from core.models import Account, RbcMonthly, Transaction
from core.utils.monthly_check import effective_tx_date, make_symbol_key

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
