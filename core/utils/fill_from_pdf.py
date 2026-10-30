"""Fill CSV gaps from stored PDF reference rows (phase 4, step 14-0d).

Maps ``RbcMonthlyTransaction`` rows to CSV ``Transaction`` type codes and
plans, per failing monthly-check period, which slice rows to remove and which
PDF rows to fill in. The check itself is never changed: a period is filled
only if the monthly check passes after the change (see
``core/utils/monthly_check.py``).

Nothing here writes to the database — only ``plan_period()``'s caller (the
``fill_from_pdf --apply`` command) does. Output lines contain account names,
dates, types, symbols and amounts only — never account digits.

Plan: docs/plans/fill-from-pdf.md (Decisions: tdw, Oct 30).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from core.models import (
    BrokerStatement,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
    Transaction,
)
from core.utils.monthly_check import (
    QTY_TOL,
    SYMBOL_ALIASES,
    _option_code_map,
    align_cash_rows,
    check_statement,
    effective_tx_date,
    normalize_option_text,
    period_transactions,
)


class UnmappedActivity(ValueError):
    """A PDF activity type outside the mapping table — stops that period."""


# PDF activity_type -> (Transaction.type, quantity sign: +1 / -1 / 0 = no
# quantity). ADJUST, RTC and OTHER are deliberately absent: unmapped rows stop
# their period and are reported, never guessed. TFR OUT maps to TRANSFER —
# RBC's CSV calls transfers between accounts Transfers (decision 2).
MAPPING = {
    "BOUGHT": ("BUY", +1),
    "SOLD": ("SELL", -1),
    "DIVIDEND": ("DIV", 0),
    "DIST": ("DIST", 0),
    "DIV REIN": ("DIV", +1),
    "INTEREST_RECEIVED": ("INT", +1),
    "INTEREST_PAID": ("INT", 0),
    "DEPOSIT": ("DEP", 0),
    "CONTRIB": ("DEP", 0),
    "WITHDRAWAL": ("WDRAW", 0),
    "TFR OUT": ("TRANSFER", 0),
    "WIRE TFR": ("TRANSFER", 0),
    "TRANSFER": ("TRANSFER", 0),
    "FEE": ("FEE", 0),
}

# Date slack for the zero-net quantity match (decision 1): same as
# align_cash_rows.
DATE_SLACK_DAYS = 5


def derive_pdf_symbol(pdf, prev_row=None):
    """Symbol for a PDF row that carries none (real-data fix, Oct 30).

    Most PDF rows have no symbol — only the security name in the description
    (e.g. 'VANGUARD FTSE CDN HIGH DIVID YIELD INDEX ETF TR UNIT'). Match the
    description against the security names of the ``RbcMonthlyHolding`` rows
    of the same statement and, when given, of the previous stored statement
    of that account: normalised (upper case, stripped), the holding name at
    the start of the description or the reverse. Returns the symbol only
    when exactly one distinct symbol matches; otherwise '' — the row is left
    without a symbol and the period then fails its after-check and is
    reported. Downstream, the check's aliases and option normalisation apply
    to the stored symbol as usual.
    """
    explicit = (pdf.symbol or "").strip()
    if explicit:
        return explicit
    desc = (pdf.description or "").strip().upper()
    if not desc:
        return ""
    rbc_pks = {pdf.rbc_monthly_id}
    if prev_row is not None:
        rbc_pks.add(prev_row.pk)
    matched = set()
    for security, symbol in RbcMonthlyHolding.objects.filter(
        rbc_monthly__in=rbc_pks
    ).values_list("security", "symbol"):
        name = (security or "").strip().upper()
        sym = (symbol or "").strip()
        if not name or not sym:
            continue
        if desc.startswith(name) or name.startswith(desc):
            matched.add(sym)
    return matched.pop() if len(matched) == 1 else ""


def _signed_qty(tx_type, qty_sign, qty):
    """Signed quantity for a filled row.

    Real-data fix (Oct 30): BUY rows always get +abs(quantity) and SELL rows
    -abs(quantity), whatever sign the PDF row has (the dry run produced
    SELL qty=10 and SELL qty=1430). Every other type keeps the mapping
    table's convention.
    """
    if tx_type == "BUY":
        return abs(qty)
    if tx_type == "SELL":
        return -abs(qty)
    return qty * Decimal(qty_sign)


def _base_transaction(account, pdf, prev_row=None):
    """Common fields of a filled row (statement/tx_hash set by the caller)."""
    return Transaction(
        account=account,
        date=pdf.date,
        settlement_date=pdf.date,
        symbol=derive_pdf_symbol(pdf, prev_row),
        currency=pdf.currency,
        price=pdf.price or Decimal("0"),
        commission=Decimal("0"),
        exchange_rate_cad=Decimal("1"),
        notes=f"{pdf.description} [source=pdf-fill]".strip(),
    )


def pdf_rows_to_transactions(pdf_rows, account, prev_group=None):
    """Map PDF activity rows to unsaved Transaction objects.

    Raises :class:`UnmappedActivity` for any activity type outside
    ``MAPPING`` (the period stops; nothing is guessed). Rows with neither
    cash nor quantity produce nothing. A row carrying both a credit and a
    debit (NRT withholding) becomes two rows — the income row (``credit``,
    mapped by the activity type) and a ``TAX`` row (``-debit``) — as the CSV
    does. A zero-net row with a quantity keeps the PDF quantity signed by the
    type's convention (SOLD negative, every other type positive) with amount
    0 (decision 1); the NRT split does not apply to it.

    ``prev_group``: the previous statement group of the account — its rows
    let a symbol-less PDF row fall back to the previous stored statement's
    holdings (real-data fix, Oct 30).
    """
    out = []
    for pdf in pdf_rows:
        if pdf.activity_type not in MAPPING:
            raise UnmappedActivity(
                f"{pdf.date} {pdf.activity_type}: no CSV type mapping — "
                f"period stopped, row not filled"
            )
        prev_row = None
        if prev_group is not None:
            prev_row = prev_group.rows.get(pdf.currency)
        tx_type, qty_sign = MAPPING[pdf.activity_type]
        credit = pdf.credit or Decimal("0")
        debit = pdf.debit or Decimal("0")
        net = credit - debit
        qty = pdf.quantity or Decimal("0")

        if net == 0:
            if qty == 0:
                # Neither cash nor quantity — stays out of the fill.
                continue
            tx = _base_transaction(account, pdf, prev_row)
            tx.type = tx_type
            tx.quantity = _signed_qty(tx_type, qty_sign, qty)
            tx.amount = Decimal("0")
            tx.amount_cad = Decimal("0")
            out.append(tx)
            continue

        if credit and debit:
            # NRT withholding: income row (credit) + TAX row (-debit).
            tx = _base_transaction(account, pdf, prev_row)
            tx.type = tx_type
            tx.quantity = _signed_qty(tx_type, qty_sign, qty)
            tx.amount = credit
            tx.amount_cad = credit
            out.append(tx)

            tax = _base_transaction(account, pdf, prev_row)
            tax.type = "TAX"
            tax.quantity = Decimal("0")
            tax.amount = -debit
            tax.amount_cad = -debit
            out.append(tax)
            continue

        tx = _base_transaction(account, pdf, prev_row)
        tx.type = tx_type
        tx.quantity = _signed_qty(tx_type, qty_sign, qty)
        tx.amount = net
        tx.amount_cad = net
        out.append(tx)
    return out


def normalise_symbol(symbol, option_map):
    """One comparison key per symbol (same rules as the monthly check).

    CSV option codes map to their normalised option text from the account's
    transaction notes; option text is normalised in place; known aliases
    (``SYMBOL_ALIASES``) apply last.
    """
    s = (symbol or "").strip()
    if s in option_map:
        return option_map[s]
    normalised = normalize_option_text(s)
    if normalised is not None:
        return normalised
    return SYMBOL_ALIASES.get(s.upper(), s)


# BrokerStatement raw_filename of the cleaned-CSV slice rows that a fill
# removes and replaces with the mapped PDF rows (plan decision 2).
SLICE_FILENAME = "rbc-act-cleaned-slice.csv"


@dataclass
class PeriodPlan:
    """Read-only plan for one failing statement group.

    ``slice_rows``: period transactions to remove (cleaned-CSV slice rows).
    ``candidates``: period transactions minus the slice rows. ``unmatched_pdf``:
    PDF rows to fill in (unmatched cash rows + unmatched zero-net rows with a
    quantity). ``new_rows``: mapped unsaved Transactions (None when stopped).
    ``unmapped``: one line per unmapped activity type — non-empty means the
    period stops and nothing changes for it. ``after``: simulated verdicts
    (None when stopped); the period is filled only if every currency PASSES.
    """

    group: object  # StatementGroup
    before: object  # StatementCheck
    slice_rows: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    unmatched_pdf: list = field(default_factory=list)
    new_rows: object = None  # [Transaction] or None when stopped
    unmapped: list = field(default_factory=list)
    after: object = None  # StatementCheck or None when stopped


def plan_period(group, prev_group, bounds, account):
    """Plan the fill for one FAILING statement group (read-only).

    Slice rows of the period are removed and replaced by the mapped PDF rows
    that ``align_cash_rows`` (per currency) and ``match_zero_net_rows``
    (decision 1) show as unmatched on the candidate set. The after verdicts
    are computed in memory via ``check_statement(txs=...)``; any unmapped
    activity type stops the period (reported, never guessed).
    """
    before = check_statement(group, prev_group, bounds)
    all_txs = period_transactions(
        group.account_pk, None, prev_group.statement_date, group.statement_date
    )
    slice_pks = set(
        BrokerStatement.objects.filter(raw_filename=SLICE_FILENAME).values_list(
            "pk", flat=True
        )
    )
    slice_rows = [tx for tx in all_txs if tx.statement_id in slice_pks]
    candidates = [tx for tx in all_txs if tx.statement_id not in slice_pks]

    unmatched_pdf = []
    for cur in sorted(group.rows):
        pdf_txs = list(
            RbcMonthlyTransaction.objects.filter(rbc_monthly=group.rows[cur])
            .order_by("date", "pk")
        )
        cur_candidates = [tx for tx in candidates if tx.currency == cur]
        _unmatched_csv, unmatched_cash = align_cash_rows(cur_candidates, pdf_txs)
        _matched_zn, unmatched_zn = match_zero_net_rows(
            cur_candidates, pdf_txs, group.account_pk
        )
        unmatched_pdf.extend(unmatched_cash)
        unmatched_pdf.extend(unmatched_zn)

    try:
        new_rows = pdf_rows_to_transactions(unmatched_pdf, account, prev_group)
    except UnmappedActivity as exc:
        return PeriodPlan(
            group=group,
            before=before,
            slice_rows=slice_rows,
            candidates=candidates,
            unmatched_pdf=unmatched_pdf,
            new_rows=None,
            unmapped=[str(exc)],
            after=None,
        )

    after = check_statement(group, prev_group, bounds, txs=candidates + new_rows)
    return PeriodPlan(
        group=group,
        before=before,
        slice_rows=slice_rows,
        candidates=candidates,
        unmatched_pdf=unmatched_pdf,
        new_rows=new_rows,
        unmapped=[],
        after=after,
    )


def match_zero_net_rows(csv_txs, pdf_txs, account_pk):
    """Match zero-net PDF rows that carry a quantity against CSV rows.

    Decision 1 (tdw, Oct 30): these rows (spin-off shares, DLR journals,
    savings-fund units) matter for the holdings check but ``align_cash_rows``
    drops them (net cash 0). Match rule: equal signed quantity (±QTY_TOL),
    equal normalised symbol (the check's aliases and option normalisation)
    and ≤ 5 days date slack (PDF row's date vs the CSV row's effective date,
    as in ``align_cash_rows``). One-to-one, closest date wins.

    Returns ``(matched_csv, unmatched_pdf)``. Rows with neither cash nor
    quantity are not candidates and never appear in either list.
    """
    zero_net = [
        p
        for p in pdf_txs
        if (p.credit or Decimal("0")) - (p.debit or Decimal("0")) == 0
        and (p.quantity or Decimal("0")) != 0
    ]
    option_map = _option_code_map(account_pk)
    available = list(csv_txs)
    matched_csv = []
    unmatched_pdf = []
    for pdf in sorted(zero_net, key=lambda p: (p.date, getattr(p, "pk", 0))):
        best = None
        best_slack = None
        for tx in available:
            if abs((pdf.quantity or Decimal("0")) - (tx.quantity or Decimal("0"))) > QTY_TOL:
                continue
            if normalise_symbol(pdf.symbol, option_map) != normalise_symbol(
                tx.symbol, option_map
            ):
                continue
            slack = abs((pdf.date - effective_tx_date(tx)).days)
            if slack > DATE_SLACK_DAYS:
                continue
            if best is None or slack < best_slack:
                best, best_slack = tx, slack
        if best is not None:
            available.remove(best)
            matched_csv.append(best)
        else:
            unmatched_pdf.append(pdf)
    return matched_csv, unmatched_pdf
