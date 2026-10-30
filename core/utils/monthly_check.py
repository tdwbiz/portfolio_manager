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

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Max, Min

from core.models import RbcMonthly, Transaction

# Cash and chain amounts are compared to the cent.
AMOUNT_TOL = Decimal("0.005")
# PDF quantities are stored at 4 dp (decision 3).
QTY_TOL = Decimal("0.0001")


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


def check_statement(group, prev_group, bounds):
    """Build the StatementCheck for one group.

    Step 2 scope: FIRST (no previous statement), NO_CSV (period outside the
    global CSV coverage) and CHAIN per currency. CASH and QUANTITY are added
    in later steps.
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

    for cur in sorted(group.rows):
        if no_csv:
            result.currencies.append(CurrencyResult(currency=cur, verdict="NO_CSV"))
            continue
        failures = check_chain(group.rows[cur], prev_group)
        result.currencies.append(
            CurrencyResult(
                currency=cur,
                verdict="FAIL" if failures else "PASS",
                failures=failures,
            )
        )
    return result
