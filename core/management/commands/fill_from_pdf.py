"""Fill CSV gaps from stored PDF reference rows (phase 4, step 14-0d).

``manage.py fill_from_pdf --account NAME [--from YYYY-MM] [--to YYYY-MM]
[--apply]``

Dry run by default: for every statement period of the account that FAILS the
monthly check inside the window, reports the cleaned-CSV slice rows that would
be removed and the mapped PDF rows that would be added, with the before/after
verdicts. A period is filled only if the monthly check passes after the
change; otherwise it is left unchanged and reported with the check's remaining
differences. Without ``--apply`` nothing is written to the database.

``--apply``: one BrokerStatement for the whole run (source='RBC',
raw_filename='pdf-fill-<YYYY-MM-DD>', deleted again at the end if no period
committed any rows). Per period: one database transaction — delete slice
rows, insert the mapped rows (skipping tx_hashes that already exist, so a
second run adds nothing), re-run the monthly check through the normal DB path
and roll back if it still fails. Requires ACCOUNT_FINGERPRINT_KEY.

Output uses account names, dates, types, symbols and amounts only — never
account digits.

Plan: docs/plans/fill-from-pdf.md (Decisions: tdw, Oct 30).
"""

import calendar
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Account,
    BrokerStatement,
    Transaction,
    compute_tx_hash,
)
from core.utils.account_fingerprint import wrap_tx_hash
from core.utils.fill_from_pdf import plan_period
from core.utils.monthly_check import (
    check_statement,
    csv_coverage_bounds,
    group_statements,
    previous_groups,
)


def _parse_month(text, option):
    """'YYYY-MM' -> (first day of month, last day of month)."""
    try:
        parsed = datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise CommandError(f"{option} must be YYYY-MM, got {text!r}")
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    return parsed, date(parsed.year, parsed.month, last_day)


def _fmt_tx(tx):
    symbol = tx.symbol or "(no symbol)"
    return (
        f"{tx.date} {tx.type} {symbol} qty={tx.quantity} "
        f"amount={tx.amount} {tx.currency}"
    )


class Command(BaseCommand):
    help = (
        "Fill CSV gaps from stored PDF reference rows: per failing monthly-check "
        "period, remove the cleaned-CSV slice rows and add the mapped unmatched "
        "PDF rows. Dry run by default; a period is filled only if the monthly "
        "check passes after the change."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account", required=True,
            help="Account name (e.g. TOM_TFSA); must match exactly one account.",
        )
        parser.add_argument(
            "--from", dest="date_from", default=None,
            help="First statement month to include (YYYY-MM, filter on statement_date).",
        )
        parser.add_argument(
            "--to", dest="date_to", default=None,
            help="Last statement month to include (YYYY-MM, filter on statement_date).",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the fills (one database transaction per period, re-checked "
                 "inside it and rolled back if the monthly check still fails). "
                 "Default: dry run.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        fill_statement = None
        if apply:
            # Fail loudly before any write when the fingerprint key is missing
            # (wrap_tx_hash raises ValueError without it).
            try:
                wrap_tx_hash("preflight")
            except ValueError as exc:
                raise CommandError(
                    f"fill_from_pdf --apply requires ACCOUNT_FINGERPRINT_KEY: {exc}"
                ) from None
            run_date = date.today()
            fill_statement = BrokerStatement.objects.create(
                source="RBC",
                file_date=run_date,
                raw_filename=f"pdf-fill-{run_date.isoformat()}",
            )

        accounts = Account.objects.filter(name=options["account"])
        if accounts.count() != 1:
            raise CommandError(
                f"--account {options['account']!r} must match exactly one account "
                f"(found {accounts.count()})"
            )
        account = accounts.first()

        month_start = month_end = None
        if options["date_from"]:
            month_start, _ = _parse_month(options["date_from"], "--from")
        if options["date_to"]:
            _, month_end = _parse_month(options["date_to"], "--to")

        # Group over ALL statements of the account (the window only filters
        # which periods are reported, so a period inside the window can still
        # chain to a previous one outside it).
        groups = group_statements(Account.objects.filter(pk=account.pk))
        prev_of = previous_groups(groups)
        bounds = csv_coverage_bounds()

        failing = []  # [(StatementGroup, StatementCheck)] inside the window
        for group in groups:
            if month_start and group.statement_date < month_start:
                continue
            if month_end and group.statement_date > month_end:
                continue
            check = check_statement(group, prev_of[id(group)], bounds)
            if any(cur.verdict == "FAIL" for cur in check.currencies):
                failing.append((group, check))

        window = (
            f"{options['date_from'] or '(start)'}..{options['date_to'] or '(end)'}"
        )
        mode = "APPLY" if apply else "dry run"
        self.stdout.write(
            f"fill_from_pdf {mode} — account {account.name}, window {window}"
        )
        self.stdout.write(f"failing periods in window: {len(failing)}")

        filled = unchanged = 0
        inserted_rows = []
        for group, _before in failing:
            prev_group = prev_of[id(group)]
            plan = plan_period(group, prev_group, bounds, account)
            would_fill = self._report_period(plan)
            if apply and would_fill:
                # An earlier period's fill may have fixed this one already.
                if not any(
                    cur.verdict == "FAIL" for cur in plan.before.currencies
                ):
                    self.stdout.write("  now passing — skipped")
                    unchanged += 1
                    continue
                inserted = self._apply_period(
                    plan, account, fill_statement, prev_group
                )
                if inserted is None:
                    unchanged += 1
                else:
                    filled += 1
                    inserted_rows.extend(inserted)
            elif would_fill:
                filled += 1
            else:
                unchanged += 1

        verb = "filled" if apply else "would fill"
        self.stdout.write(
            f"summary: {len(failing)} failing — {verb} {filled}, "
            f"left unchanged {unchanged}"
        )

        if fill_statement is not None:
            if inserted_rows:
                dates = [tx.date for tx in inserted_rows]
                fill_statement.period_start = min(dates)
                fill_statement.period_end = max(dates)
                fill_statement.save()
                self.stdout.write(
                    f"BrokerStatement {fill_statement.raw_filename}: "
                    f"{len(inserted_rows)} row(s), "
                    f"{fill_statement.period_start}..{fill_statement.period_end}"
                )
            else:
                # Decision 4: no empty fill statements accumulate.
                fill_statement.delete()
                self.stdout.write(
                    "no rows committed — empty fill BrokerStatement deleted"
                )

    # ------------------------------------------------------------------
    # Reporting (dry run)
    # ------------------------------------------------------------------

    @staticmethod
    def _verdict_line(check):
        parts = [f"[{cur.currency}] {cur.verdict}" for cur in check.currencies]
        kinds = check.failed_kinds
        suffix = f" ({', '.join(kinds)})" if kinds else ""
        return " ".join(parts) + suffix

    def _failure_lines(self, check):
        return [line for cur in check.currencies for line in cur.failures]

    def _report_period(self, plan):
        """Print the report for one failing period.

        Returns True when the dry-run simulation would fill the period
        (after-check passes), False when it is left unchanged.
        """
        out = self.stdout.write
        group = plan.group
        out(f"== {group.statement_date} ==")
        out(f"  verdict before: {self._verdict_line(plan.before)}")

        if plan.unmapped:
            for line in plan.unmapped:
                out(f"  STOPPED (unmapped activity): {line}")
            for line in self._failure_lines(plan.before):
                out(f"    {line}")
            out("  left unchanged")
            return False

        for line in plan.timing_pairs:
            out(f"  {line} — not filled")

        for tx in plan.slice_rows:
            out(f"  remove: {_fmt_tx(tx)}")
        for tx in plan.new_rows:
            out(f"  add:    {_fmt_tx(tx)}")
        if not plan.slice_rows and not plan.new_rows:
            out("  nothing to fill (no unmatched PDF rows)")

        after = plan.after
        out(f"  verdict after:  {self._verdict_line(after)}")
        if any(cur.verdict == "FAIL" for cur in after.currencies):
            for line in self._failure_lines(after):
                out(f"    {line}")
            out("  left unchanged (after-check still fails)")
            return False
        return True

    # ------------------------------------------------------------------
    # Apply path (--apply)
    # ------------------------------------------------------------------

    @staticmethod
    def _tx_hash(account, tx):
        """Wrapped hash with the 'PDF' broker label — disjoint from CSV ones."""
        return wrap_tx_hash(
            compute_tx_hash(
                broker="PDF",
                account_id=str(account.pk),
                date=tx.date,
                tx_type=tx.type,
                symbol=tx.symbol,
                quantity=tx.quantity,
                price=tx.price,
                currency=tx.currency,
                amount=tx.amount,
                settlement_date=tx.settlement_date,
            )
        )

    def _apply_period(self, plan, account, fill_statement, prev_group):
        """Apply one planned period inside a single database transaction.

        Deletes the slice rows, inserts the mapped rows (skipping tx_hashes
        that already exist), re-runs the monthly check through the normal DB
        path and rolls back when it still fails. Returns the list of inserted
        Transactions, or None when the period was rolled back.
        """
        out = self.stdout.write
        group = plan.group
        try:
            with transaction.atomic():
                for tx in plan.slice_rows:
                    tx.delete()
                inserted = []
                for tx in plan.new_rows:
                    tx_hash = self._tx_hash(account, tx)
                    if Transaction.objects.filter(tx_hash=tx_hash).exists():
                        continue  # already filled — a second run adds nothing
                    tx.statement = fill_statement
                    tx.tx_hash = tx_hash
                    tx.save()
                    inserted.append(tx)
                # Fresh bounds: the inserted rows may widen CSV coverage.
                check = check_statement(
                    group, prev_group, csv_coverage_bounds()
                )
                if any(cur.verdict == "FAIL" for cur in check.currencies):
                    raise _AfterCheckFailed(self._failure_lines(check))
        except _AfterCheckFailed as exc:
            for line in exc.lines:
                out(f"    {line}")
            out("  rolled back (after-check still fails)")
            return None
        out(
            f"  FILLED: {len(inserted)} row(s) added, "
            f"{len(plan.slice_rows)} removed"
        )
        return inserted


class _AfterCheckFailed(Exception):
    """Internal: the monthly check still fails inside the apply transaction."""

    def __init__(self, lines):
        super().__init__("; ".join(lines))
        self.lines = lines
