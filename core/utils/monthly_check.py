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
