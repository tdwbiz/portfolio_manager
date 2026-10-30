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

from decimal import Decimal

from core.models import Transaction
from core.utils.monthly_check import (
    QTY_TOL,
    SYMBOL_ALIASES,
    _option_code_map,
    effective_tx_date,
    normalize_option_text,
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


def _base_transaction(account, pdf):
    """Common fields of a filled row (statement/tx_hash set by the caller)."""
    return Transaction(
        account=account,
        date=pdf.date,
        settlement_date=pdf.date,
        symbol=(pdf.symbol or "").strip(),
        currency=pdf.currency,
        price=pdf.price or Decimal("0"),
        commission=Decimal("0"),
        exchange_rate_cad=Decimal("1"),
        notes=f"{pdf.description} [source=pdf-fill]".strip(),
    )


def pdf_rows_to_transactions(pdf_rows, account):
    """Map PDF activity rows to unsaved Transaction objects.

    Raises :class:`UnmappedActivity` for any activity type outside
    ``MAPPING`` (the period stops; nothing is guessed). Rows with neither
    cash nor quantity produce nothing. A row carrying both a credit and a
    debit (NRT withholding) becomes two rows — the income row (``credit``,
    mapped by the activity type) and a ``TAX`` row (``-debit``) — as the CSV
    does. A zero-net row with a quantity keeps the PDF quantity signed by the
    type's convention (SOLD negative, every other type positive) with amount
    0 (decision 1); the NRT split does not apply to it.
    """
    out = []
    for pdf in pdf_rows:
        if pdf.activity_type not in MAPPING:
            raise UnmappedActivity(
                f"{pdf.date} {pdf.activity_type}: no CSV type mapping — "
                f"period stopped, row not filled"
            )
        tx_type, qty_sign = MAPPING[pdf.activity_type]
        credit = pdf.credit or Decimal("0")
        debit = pdf.debit or Decimal("0")
        net = credit - debit
        qty = pdf.quantity or Decimal("0")

        if net == 0:
            if qty == 0:
                # Neither cash nor quantity — stays out of the fill.
                continue
            sign = -1 if qty_sign == -1 else +1
            tx = _base_transaction(account, pdf)
            tx.type = tx_type
            tx.quantity = qty * sign
            tx.amount = Decimal("0")
            tx.amount_cad = Decimal("0")
            out.append(tx)
            continue

        if credit and debit:
            # NRT withholding: income row (credit) + TAX row (-debit).
            tx = _base_transaction(account, pdf)
            tx.type = tx_type
            tx.quantity = qty * Decimal(qty_sign)
            tx.amount = credit
            tx.amount_cad = credit
            out.append(tx)

            tax = _base_transaction(account, pdf)
            tax.type = "TAX"
            tax.quantity = Decimal("0")
            tax.amount = -debit
            tax.amount_cad = -debit
            out.append(tax)
            continue

        tx = _base_transaction(account, pdf)
        tx.type = tx_type
        tx.quantity = qty * Decimal(qty_sign)
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
