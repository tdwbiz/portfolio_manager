"""CSV-vs-RBC monthly check report (phase 4, steps 14a/14b).

``manage.py monthly_check [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE``
``manage.py monthly_check --store [...]``
``manage.py monthly_check --accept --reason TEXT [--id N] [--account NAME] [--kind K] [--ref R|PREFIX*] [--from YYYY-MM] [--to YYYY-MM]``
``manage.py monthly_check --reopen --id N --reason TEXT``

Compares the CSV ``Transaction`` rows with the stored RBC monthly statements
and reports, per account / statement / currency, a verdict (FIRST / NO_CSV /
PASS / FAIL), plus an import-log section for errored PDFs.

Without ``--store`` the command is read-only: nothing is written to the
database and the only file written is ``--out`` (required in report mode).
With ``--store`` the same checks run and their results are persisted as
``MonthlyCheckResult`` rows and exceptions as ``MonthlyException`` rows
(upserts; vanished exceptions resolve, accepted ones reopen when their
difference changes); ``--out`` is optional then.

``--accept`` / ``--reopen`` touch the database only (no checks run, no
report): accepting an exception is a decision — a note with a required
reason, never a data change. ``--accept`` accepts only open exceptions
matching all filters; ``--ref`` takes an exact ref or a prefix ending in
``*`` (e.g. ``RBF*``).

Plan: docs/plans/monthly-check-store.md (Decisions: tdw, Oct 30).
"""

import calendar
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Account, MonthlyException
from core.utils.monthly_check import (
    check_statement,
    csv_coverage_bounds,
    group_statements,
    previous_groups,
    scan_import_logs,
)
from core.utils.monthly_check_store import (
    exceptions_from_check,
    import_exceptions_from_parked,
    store_exceptions,
    store_results,
)


def _parse_month(text, option):
    """'YYYY-MM' -> (first day of month, last day of month)."""
    try:
        parsed = datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise CommandError(f"{option} must be YYYY-MM, got {text!r}")
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    return parsed, date(parsed.year, parsed.month, last_day)


class Command(BaseCommand):
    help = (
        "CSV-vs-RBC report: per account/statement/currency verdict "
        "(FIRST / NO_CSV / PASS / FAIL) plus an import-log section. "
        "Read-only by default (only the --out report file is written); "
        "--store persists results and exceptions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account", default=None,
            help="Account name (e.g. TOM_TFSA). Default: all accounts.",
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
            "--out", default=None,
            help="Path for the full report file (required in report mode; "
                 "optional with --store).",
        )
        parser.add_argument(
            "--store", action="store_true",
            help="Persist the check results and exceptions "
                 "(MonthlyCheckResult / MonthlyException).",
        )
        parser.add_argument(
            "--accept", action="store_true",
            help="Accept open exceptions matching all filters (note only, "
                 "no data change). Requires --reason.",
        )
        parser.add_argument(
            "--reopen", action="store_true",
            help="Reopen one exception. Requires --id N and --reason.",
        )
        parser.add_argument(
            "--reason", default=None,
            help="Reason note (required with --accept / --reopen).",
        )
        parser.add_argument(
            "--id", dest="row_id", type=int, default=None,
            help="Exception id (--reopen: required; --accept: filter).",
        )
        parser.add_argument(
            "--kind", choices=[k for k, _ in MonthlyException.KIND_CHOICES],
            default=None,
            help="Filter --accept by exception kind.",
        )
        parser.add_argument(
            "--ref", default=None,
            help="Filter --accept by ref: exact (e.g. RBF2010) or a prefix "
                 "ending in * (e.g. RBF*).",
        )

    def handle(self, *args, **options):
        if options["accept"] or options["reopen"]:
            # Decision mode: DB only — no checks run, no report written.
            if options["store"]:
                raise CommandError(
                    "--accept/--reopen cannot be combined with --store"
                )
            if options["accept"]:
                self._accept(options)
            else:
                self._reopen(options)
            return
        if not options["store"] and not options["out"]:
            raise CommandError("--out is required in report mode (without --store)")

        month_start = month_end = None
        if options["date_from"]:
            month_start, _ = _parse_month(options["date_from"], "--from")
        if options["date_to"]:
            _, month_end = _parse_month(options["date_to"], "--to")

        accounts = Account.objects.all().order_by("name")
        if options["account"]:
            accounts = accounts.filter(name=options["account"])

        # Group over ALL statements of the selected accounts (the window only
        # filters which statements are reported, so a statement inside the
        # window can still chain to a previous one outside it).
        groups = group_statements(accounts)
        prev_of = previous_groups(groups)
        bounds = csv_coverage_bounds()

        results = []  # [(StatementGroup, StatementCheck)] inside the window
        for group in groups:
            if month_start and group.statement_date < month_start:
                continue
            if month_end and group.statement_date > month_end:
                continue
            results.append(
                (group, check_statement(group, prev_of[id(group)], bounds))
            )

        imports = scan_import_logs()

        if options["store"]:
            self._store(results, imports)
        if options["out"]:
            self._write_report(options["out"], options, bounds, results, groups, imports)
        self._print_summary(accounts, results, groups, imports)

    # ------------------------------------------------------------------
    # --accept / --reopen
    # ------------------------------------------------------------------

    def _month_window(self, options):
        month_start = month_end = None
        if options["date_from"]:
            month_start, _ = _parse_month(options["date_from"], "--from")
        if options["date_to"]:
            _, month_end = _parse_month(options["date_to"], "--to")
        return month_start, month_end

    def _accept(self, options):
        """Accept open exceptions matching all filters (a note only)."""
        reason = (options["reason"] or "").strip()
        if not reason:
            raise CommandError("--accept requires --reason TEXT")

        qs = MonthlyException.objects.filter(
            status=MonthlyException.STATUS_OPEN
        ).select_related("account")
        if options["row_id"] is not None:
            qs = qs.filter(pk=options["row_id"])
        if options["account"]:
            qs = qs.filter(account__name=options["account"])
        if options["kind"]:
            qs = qs.filter(kind=options["kind"])
        ref = options["ref"]
        if ref:
            if ref.endswith("*"):
                qs = qs.filter(ref__startswith=ref[:-1])
            else:
                qs = qs.filter(ref=ref)
        month_start, month_end = self._month_window(options)
        if month_start is not None:
            qs = qs.filter(statement_date__gte=month_start)
        if month_end is not None:
            qs = qs.filter(statement_date__lte=month_end)

        now = timezone.now()
        per = {}
        count = 0
        for row in qs:
            row.status = MonthlyException.STATUS_ACCEPTED
            row.reason = reason
            row.accepted_at = now
            row.save()
            key = (row.account.name, row.kind)
            per[key] = per.get(key, 0) + 1
            count += 1
        self.stdout.write(f"Accepted {count} exception(s)")
        for (name, kind), n in sorted(per.items()):
            self.stdout.write(f"  {name} {kind}: {n}")

    def _reopen(self, options):
        """Reopen one exception: status open + reason note appended to detail."""
        if options["row_id"] is None:
            raise CommandError("--reopen requires --id N")
        reason = (options["reason"] or "").strip()
        if not reason:
            raise CommandError("--reopen requires --reason TEXT")
        row = (
            MonthlyException.objects.filter(pk=options["row_id"])
            .select_related("account")
            .first()
        )
        if row is None:
            raise CommandError(f"no exception with id {options['row_id']}")
        now = timezone.now()
        row.status = MonthlyException.STATUS_OPEN
        row.resolved_at = None
        row.accepted_at = None
        note = f"[reopened {now:%Y-%m-%d}: {reason}]"
        row.detail = (row.detail + "\n" if row.detail else "") + note
        row.save()
        self.stdout.write(
            f"Reopened exception {row.pk} ({row.account.name} "
            f"{row.statement_date} {row.kind})"
        )

    # ------------------------------------------------------------------
    # --store
    # ------------------------------------------------------------------

    def _store(self, results, imports):
        """Persist verdicts and exceptions (idempotent upserts).

        IMPORT exceptions come from the parked list only — conflicts are
        never passed in; parked entries whose account name matches no
        Account are printed as skipped and not stored (decision 4).
        Check exceptions and import exceptions for the same
        (account, statement_date) are stored together, so resolve-vanished
        sees the full set.
        """
        written = store_results(results)
        self.stdout.write(f"Stored {written} check results")

        accounts = list(Account.objects.all())
        by_pk = {a.pk: a for a in accounts}
        by_name = {a.name: a for a in accounts}
        import_excs, skipped = import_exceptions_from_parked(imports["parked"])
        for entry in skipped:
            self.stdout.write(
                f"  skipped (unknown account): {entry['filename']}"
            )

        per_key = {}
        for group, check in results:
            per_key.setdefault(
                (group.account_pk, group.statement_date), []
            ).extend(exceptions_from_check(check))
        for exc in import_excs:  # unknown names were skipped by the helper
            account = by_name[exc["account_name"]]
            per_key.setdefault(
                (account.pk, exc["statement_date"]), []
            ).append(exc)

        totals = [0, 0, 0, 0]
        for pk, statement_date in sorted(per_key):
            created, updated, reopened, resolved = store_exceptions(
                by_pk[pk], statement_date, per_key[(pk, statement_date)]
            )
            totals = [a + b for a, b in zip(totals, (created, updated, reopened, resolved))]
        self.stdout.write(
            f"Exceptions: created={totals[0]} updated={totals[1]} "
            f"reopened={totals[2]} resolved={totals[3]}"
        )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _fmt_bounds(self, bounds):
        first, last = bounds
        return (f"first settlement {first}, last settlement {last}"
                if first is not None else "no Transactions with a settlement date")

    def _statement_lines(self, group, check):
        lines = []
        for cur in check.currencies:
            line = f"  {check.statement_date} [{cur.currency}] {cur.verdict}"
            lines.append(line)
            lines.extend(f"    {f}" for f in cur.failures)
            lines.extend(f"    note: {n}" for n in cur.notes)
        return lines

    def _duplicate_lines(self, groups):
        """One line per duplicate (statement_date, currency) — a finding
        (decision 4): it should not happen after the content-hash re-import.
        Listed for every grouped statement of the selected accounts, not
        just those inside the month window."""
        lines = []
        for group in groups:
            for currency in sorted(group.duplicates):
                files = group.duplicates[currency]
                lines.append(
                    f"  {group.account_name} {group.statement_date} [{currency}]: "
                    f"{len(files)} rows — files: {', '.join(files)} "
                    f"(used newest parsed_at)"
                )
        return lines

    def _pair_lines(self, results):
        """CSV-only <-> PDF-only symbol pairs (for a future symbol map)."""
        lines = []
        for group, check in results:
            for csv_sym, pdf_sym in check.symbol_pairs:
                left = f"CSV '{csv_sym}'" if csv_sym else "(no CSV-only symbol)"
                right = f"PDF '{pdf_sym}'" if pdf_sym else "(no PDF-only symbol)"
                lines.append(
                    f"  {check.account_name} {check.statement_date}: {left} <-> {right}"
                )
        return lines

    def _import_lines(self, imports):
        labels = [
            ("parked", "parked (monthly, error, never stored)"),
            ("conflicts", "conflicts (second copies of corrected statements)"),
            ("annual", "annual errors"),
            ("other", "other errors"),
        ]
        lines = ["== Import log =="]
        for key, label in labels:
            entries = imports[key]
            lines.append(f"  {label}: {len(entries)}")
            for entry in entries:
                lines.append(
                    f"    {entry['filename']}: status={entry['status']} — "
                    f"{entry['reason']}"
                )
        return lines

    def _write_report(self, out_path, options, bounds, results, groups, imports):
        lines = [
            "monthly_check report",
            f"filters: account={options['account'] or '(all)'} "
            f"from={options['date_from'] or '(none)'} to={options['date_to'] or '(none)'}",
            f"CSV coverage (global): {self._fmt_bounds(bounds)}",
            "",
            "== Statements ==",
        ]
        by_account = {}
        for group, check in results:
            by_account.setdefault(check.account_name, []).append((group, check))
        for account_name in sorted(by_account):
            lines.append(account_name)
            for group, check in by_account[account_name]:
                lines.extend(self._statement_lines(group, check))
            lines.append("")

        dup_lines = self._duplicate_lines(groups)
        lines.append("== Duplicate statement rows (findings) ==")
        lines.extend(dup_lines if dup_lines else ["  none"])
        lines.append("")

        pair_lines = self._pair_lines(results)
        lines.append("== Symbol pairs (CSV-only vs PDF-only) ==")
        lines.extend(pair_lines if pair_lines else ["  none"])
        lines.append("")

        lines.extend(self._import_lines(imports))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")

    def _print_summary(self, accounts, results, groups, imports):
        per_account = {}
        for _group, check in results:
            entry = per_account.setdefault(
                check.account_name,
                {"FIRST": 0, "NO_CSV": 0, "PASS": 0, "FAIL": 0, "kinds": set()},
            )
            for cur in check.currencies:
                entry[cur.verdict] += 1
            entry["kinds"].update(check.failed_kinds)

        self.stdout.write(f"Accounts: {accounts.count()}")
        self.stdout.write(f"Statements checked: {len(results)}")
        totals = {"FIRST": 0, "NO_CSV": 0, "PASS": 0, "FAIL": 0}
        for account_name in sorted(per_account):
            entry = per_account[account_name]
            for verdict in totals:
                totals[verdict] += entry[verdict]
            kinds = (f" ({', '.join(sorted(entry['kinds']))})"
                     if entry["FAIL"] else "")
            self.stdout.write(
                f"  {account_name}: FIRST={entry['FIRST']} NO_CSV={entry['NO_CSV']} "
                f"PASS={entry['PASS']} FAIL={entry['FAIL']}{kinds}"
            )
        self.stdout.write(
            f"Totals: FIRST={totals['FIRST']} NO_CSV={totals['NO_CSV']} "
            f"PASS={totals['PASS']} FAIL={totals['FAIL']}"
        )
        dup_lines = self._duplicate_lines(groups)
        self.stdout.write(f"Duplicate statement rows (findings): {len(dup_lines)}")
        self.stdout.writelines(f"{line}\n" for line in dup_lines)
        self.stdout.write(
            f"Import log: parked={len(imports['parked'])} "
            f"conflicts={len(imports['conflicts'])} annual={len(imports['annual'])} "
            f"other={len(imports['other'])}"
        )
