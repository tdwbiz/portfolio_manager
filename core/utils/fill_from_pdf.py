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
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q

from core.models import (
    BrokerStatement,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
    Transaction,
)
from core.utils.monthly_check import (
    AMOUNT_TOL,
    INCOME_TYPES,
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

# How far outside the period an existing CSV row may be dated while still
# counting as the timing pair of an unmatched PDF row (real-data fix, Oct 30).
TIMING_PAIR_DAYS = 35


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

    Real-data fix 7 (Oct 30): a description containing 'U S DLR' (HORIZONS
    U S DLR CURRENCY ETF — the Norbert's gambit journal) gets the symbol
    DLR on both the CAD and the USD side, before the holdings name match.
    """
    explicit = (pdf.symbol or "").strip()
    if explicit:
        return explicit
    desc = (pdf.description or "").strip().upper()
    if not desc:
        return ""
    if "U S DLR" in desc:
        return "DLR"
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


def _holding_deltas(cur_rbc_pk, prev_rbc_pk, currency):
    """Per-symbol holding quantity changes (cur - prev) between statements.

    Real-data fix 6 (Oct 30): the change of each same-currency holding
    between the previous stored statement and this one; a holding absent on
    one side counts as 0 there. Symbols are taken from the holdings' own
    ``symbol`` field (blank ones are skipped).
    """
    def qty_map(pk):
        return {
            sym: qty
            for sym, qty in RbcMonthlyHolding.objects.filter(
                rbc_monthly_id=pk, currency=currency
            ).values_list("symbol", "quantity")
            if (sym or "").strip()
        }

    cur = qty_map(cur_rbc_pk)
    prev = qty_map(prev_rbc_pk) if prev_rbc_pk is not None else {}
    return [
        (sym, cur.get(sym, Decimal("0")) - prev.get(sym, Decimal("0")))
        for sym in set(cur) | set(prev)
    ]


def _symbol_by_quantity(pdf, prev_row, tx_type, qty, used):
    """Symbol from the holdings change when the name match found none.

    Real-data fix 6 (Oct 30): a SELL whose abs(quantity) equals the drop of
    exactly one holding between the previous stored statement and this one
    (e.g. BMO 52 to 0) gets that holding's symbol; a BUY whose quantity
    equals the rise of exactly one holding does the same. Only a unique
    match counts, and each holding change is used once per period —
    ``used`` is the set of (rbc_monthly pk, symbol) changes already
    consumed. Returns '' without a previous statement, or when no holding
    matches exactly / more than one does.
    """
    if tx_type not in ("BUY", "SELL") or not qty:
        return ""
    prev_pk = prev_row.pk if prev_row is not None else None
    if prev_pk is None:
        return ""
    direction = Decimal("-1") if tx_type == "SELL" else Decimal("1")
    target = abs(qty) * direction
    matches = [
        sym
        for sym, delta in _holding_deltas(
            pdf.rbc_monthly_id, prev_pk, pdf.currency
        )
        if delta == target and (pdf.rbc_monthly_id, sym) not in used
    ]
    if len(matches) != 1:
        return ""
    used.add((pdf.rbc_monthly_id, matches[0]))
    return matches[0]


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


def _base_transaction(account, pdf, prev_row=None, symbol=None):
    """Common fields of a filled row (statement/tx_hash set by the caller).

    ``symbol``: pre-resolved symbol (explicit / name match / holdings
    change); when None it is derived from the holdings' security names.
    """
    if symbol is None:
        symbol = derive_pdf_symbol(pdf, prev_row)
    return Transaction(
        account=account,
        date=pdf.date,
        settlement_date=pdf.date,
        symbol=symbol,
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
    holdings (real-data fix, Oct 30) and supply the holdings change for the
    quantity-based symbol match (real-data fix 6).
    """
    out = []
    used_symbols = set()  # (rbc_monthly pk, symbol) holding changes consumed
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

        # Symbol: explicit / holdings name match first, then the holdings
        # change by quantity; each holding change is used once per period.
        symbol = derive_pdf_symbol(pdf, prev_row) or _symbol_by_quantity(
            pdf, prev_row, tx_type, qty, used_symbols
        )

        if net == 0:
            if qty == 0:
                # Neither cash nor quantity — stays out of the fill.
                continue
            tx = _base_transaction(account, pdf, prev_row, symbol=symbol)
            tx.type = tx_type
            tx.quantity = _signed_qty(tx_type, qty_sign, qty)
            tx.amount = Decimal("0")
            tx.amount_cad = Decimal("0")
            out.append(tx)
            continue

        if credit and debit:
            # NRT withholding: income row (credit) + TAX row (-debit).
            tx = _base_transaction(account, pdf, prev_row, symbol=symbol)
            tx.type = tx_type
            tx.quantity = _signed_qty(tx_type, qty_sign, qty)
            tx.amount = credit
            tx.amount_cad = credit
            out.append(tx)

            tax = _base_transaction(account, pdf, prev_row, symbol=symbol)
            tax.type = "TAX"
            tax.quantity = Decimal("0")
            tax.amount = -debit
            tax.amount_cad = -debit
            out.append(tax)
            continue

        tx = _base_transaction(account, pdf, prev_row, symbol=symbol)
        tx.type = tx_type
        tx.quantity = _signed_qty(tx_type, qty_sign, qty)
        tx.amount = net
        tx.amount_cad = net
        out.append(tx)
    return out


def _timing_pair_match(account_pk, currency, net, period_start, period_end):
    """Existing CSV row with the same net amount, dated outside this period.

    Real-data fix (Oct 30): RBC sometimes prints a flow in the next
    statement's month while the CSV dates it in the previous one (the dry run
    would have added SUSAN_RRIF VO 281.29 in July 2020 while the CSV has it
    in June, and SUSAN_TFSA QQQ 31.12 minus 4.66 in Nov 2019 while the CSV
    has 26.46 in October). Such a row must not be filled again — it is
    reported as a timing pair instead.

    Returns the matching Transaction (effective date outside
    (period_start, period_end] and within ``TIMING_PAIR_DAYS`` of either
    boundary, same account/currency/net amount ±0.005), else None.
    """
    lo = min(period_start, period_end) - timedelta(days=TIMING_PAIR_DAYS)
    hi = max(period_start, period_end) + timedelta(days=TIMING_PAIR_DAYS)
    qs = Transaction.objects.filter(
        Q(date__gte=lo, date__lte=hi)
        | Q(settlement_date__gte=lo, settlement_date__lte=hi),
        account_id=account_pk,
        currency=currency,
    )
    for tx in qs:
        eff = effective_tx_date(tx)
        if period_start < eff <= period_end:
            continue
        dist = min(abs((eff - period_start).days), abs((period_end - eff).days))
        if dist > TIMING_PAIR_DAYS:
            continue
        if abs(net - tx.amount) <= AMOUNT_TOL:
            return tx
    return None


def _is_income_row(pdf):
    """True when the PDF row maps to an income type (DIV / DIST / INT).

    Real-data fix 5 (Oct 30): timing pairs exist only for income rows and
    the TAX split from them — deposits, withdrawals and transfers repeat
    with the same amount and are never timing pairs.
    """
    entry = MAPPING.get(pdf.activity_type)
    return entry is not None and entry[0] in INCOME_TYPES


def split_timing_pairs(unmatched_cash, account_pk, currency, period_start,
                       period_end):
    """Split unmatched cash PDF rows into (rows_to_fill, report lines).

    Real-data fix (Oct 30): an unmatched row — or the PDF rows of one date
    and symbol taken together by their net amount (a dividend plus its NRT
    tax) — that matches an existing Transaction of the same account and
    currency with the same net amount dated within 35 days but outside this
    period is not filled; it is reported as
    'timing pair with CSV row dated <date>'. The group check runs first (a
    non-zero group sum), then each remaining row individually.

    Real-data fix 5 (Oct 30): only income rows (DIV / DIST / INT) and the
    TAX split from them — the non-income companions of a same-date/symbol
    group that contains an income row — are checked; every other unmatched
    row (deposit, withdrawal, transfer, fee) is always kept for filling.
    """
    groups = {}
    for pdf in unmatched_cash:
        groups.setdefault((pdf.date, (pdf.symbol or "").strip()), []).append(pdf)
    keep = []
    lines = []
    for (d, sym), rows in groups.items():
        label = f"{d} {sym or '(no symbol)'}"
        has_income = any(_is_income_row(r) for r in rows)
        if len(rows) > 1 and has_income:
            net = sum(((r.credit or Decimal("0")) - (r.debit or Decimal("0")))
                      for r in rows)
            if net != 0:
                tx = _timing_pair_match(account_pk, currency, net,
                                        period_start, period_end)
                if tx is not None:
                    lines.append(
                        f"{label} net {net}: timing pair with CSV row dated "
                        f"{tx.date}"
                    )
                    continue
        for r in rows:
            # Non-income rows are never timing pairs, except as the TAX-split
            # companions of a multi-row group that contains an income row.
            if not _is_income_row(r) and not (len(rows) > 1 and has_income):
                keep.append(r)
                continue
            rnet = (r.credit or Decimal("0")) - (r.debit or Decimal("0"))
            tx = _timing_pair_match(account_pk, currency, rnet,
                                    period_start, period_end)
            if tx is not None:
                lines.append(
                    f"{label} net {rnet}: timing pair with CSV row dated "
                    f"{tx.date}"
                )
            else:
                keep.append(r)
    return keep, lines


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
    period stops and nothing changes for it. ``timing_pairs``: one report
    line per unmatched PDF row left unfilled because the CSV already has it
    dated just outside the period (real-data fix, Oct 30). ``after``:
    simulated verdicts (None when stopped); the period is filled only if
    every currency PASSES.
    """

    group: object  # StatementGroup
    before: object  # StatementCheck
    slice_rows: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    unmatched_pdf: list = field(default_factory=list)
    new_rows: object = None  # [Transaction] or None when stopped
    unmapped: list = field(default_factory=list)
    timing_pairs: list = field(default_factory=list)
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
    timing_pairs = []
    period_start = prev_group.statement_date
    period_end = group.statement_date
    for cur in sorted(group.rows):
        pdf_txs = list(
            RbcMonthlyTransaction.objects.filter(rbc_monthly=group.rows[cur])
            .order_by("date", "pk")
        )
        cur_candidates = [tx for tx in candidates if tx.currency == cur]
        _unmatched_csv, unmatched_cash = align_cash_rows(cur_candidates, pdf_txs)
        unmatched_cash, pair_lines = split_timing_pairs(
            unmatched_cash, group.account_pk, cur, period_start, period_end
        )
        timing_pairs.extend(pair_lines)
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
            timing_pairs=timing_pairs,
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
        timing_pairs=timing_pairs,
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
