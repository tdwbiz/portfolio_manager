"""compare_monthly_pdf — read-only check of stored RbcMonthly rows vs a PDF.

Phase 3 step 13a-2 (docs/plans/compare-monthly-pdf.md). For each given
monthly statement file: parse it with the deterministic text parser
(``imports/parsers_pdf.py``), run its self-check, resolve the account by
fingerprint and compare the parsed fields against the stored ``RbcMonthly``
rows field by field. The command is strictly read-only — it performs no
database writes of any kind.

Output privacy (hard rule): lines carry only file names, account names,
currencies, statement dates, row ids, counts and verdicts — never amounts,
quantities, prices, account numbers or any account digits. The parsed
account number is used only for the fingerprint lookup and is never echoed.
"""

import os
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import RbcMonthly
from core.utils.account_fingerprint import lookup_account
from imports.parsers_pdf import (
    _extract_text_from_pdf,
    parse_rbc_pdf_multi,
    self_check_sections,
)

CENT = Decimal("0.01")
FOUR_DP = Decimal("0.0001")
VERDICTS = ("MATCH", "DIFFER", "ONLY_PDF", "ONLY_DB")


class Command(BaseCommand):
    help = (
        "Read-only: parse monthly statement PDFs with the text parser and "
        "compare each field against the stored RbcMonthly rows. Never writes "
        "to the database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "paths",
            nargs="+",
            type=str,
            help="Path(s) to monthly statement PDFs (or text files for tests).",
        )

    def handle(self, *args, **options):
        for path in options["paths"]:
            self._fields_compared = 0
            self._verdict_counts = {v: 0 for v in VERDICTS}
            self._process_file(path)

    # ------------------------------------------------------------------
    # Output helpers (privacy: counts, ids and verdicts only)
    # ------------------------------------------------------------------

    def _emit_field(self, prefix: str, field: str, verdict: str) -> None:
        """Print one per-field line and count its verdict for the totals."""
        self.stdout.write(f"{prefix} {field}: {verdict}")
        self._fields_compared += 1
        self._verdict_counts[verdict] += 1

    def _emit_note(self, line: str) -> None:
        """Print a non-verdict line (DUP_SYMBOLS counts etc.)."""
        self.stdout.write(line)

    def _totals_line(self, base: str) -> str:
        c = self._verdict_counts
        return (
            f"TOTALS {base}: fields compared {self._fields_compared}, "
            f"MATCH {c['MATCH']}, DIFFER {c['DIFFER']}, "
            f"ONLY_PDF {c['ONLY_PDF']}, ONLY_DB {c['ONLY_DB']}"
        )

    # ------------------------------------------------------------------
    # Per file
    # ------------------------------------------------------------------

    def _process_file(self, path: str) -> None:
        base = os.path.basename(path)
        if not os.path.exists(path):
            self.stdout.write(f"MISSING_FILE {base}")
            self.stdout.write(self._totals_line(base))
            return

        with open(path, "rb") as f:
            is_pdf = f.read(4) == b"%PDF"

        if is_pdf:
            sections = parse_rbc_pdf_multi(path)
            full_text = _extract_text_from_pdf(path)
        else:
            # Plain text (test fixtures): pass the string to the parser.
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            sections = parse_rbc_pdf_multi(text)
            full_text = text

        reasons = self_check_sections(sections, full_text)
        if reasons:
            for reason in reasons:
                self.stdout.write(f"SELF_CHECK {base}: {reason}")
        else:
            self.stdout.write(f"SELF_CHECK OK {base}")

        for section in sections:
            self._process_section(base, section)

        self.stdout.write(self._totals_line(base))

    # ------------------------------------------------------------------
    # Per currency section
    # ------------------------------------------------------------------

    def _process_section(self, base: str, section: dict) -> None:
        cur = str(section.get("currency") or "CAD").upper()

        try:
            account = lookup_account(section.get("account_number") or "")
        except ValueError as exc:
            # Fingerprint key missing — fail loudly at the point of use.
            self.stderr.write(f"ERROR {base} {cur}: {exc}")
            return
        if account is None:
            self.stdout.write(f"UNKNOWN_ACCOUNT {base} {cur}")
            return

        statement_date = section.get("statement_date")
        rows = []
        if statement_date is not None:
            rows = list(
                RbcMonthly.objects.filter(
                    account=account,
                    statement_date=statement_date,
                    currency=cur,
                ).order_by("id")
            )
        self.stdout.write(f"ROWS FOUND {len(rows)} {base} {cur}")

        self._compare_section(base, section, account, rows)

    # ------------------------------------------------------------------
    # Comparison (step 2): one line per field, verdicts only — no values
    # ------------------------------------------------------------------

    @staticmethod
    def _money(value):
        """Quantize a parsed money value to the cent; None stays None."""
        if value is None:
            return None
        return Decimal(value).quantize(CENT)

    @staticmethod
    def _four_dp(value):
        """Quantize a 4-dp column (quantity, market_price); None stays None."""
        if value is None:
            return None
        return Decimal(value).quantize(FOUR_DP)

    @classmethod
    def _pdf_cash(cls, section: dict):
        """Closing cash for the parsed currency section, or None."""
        for bal in section.get("balances") or []:
            if str(bal.get("account_type", "")).strip().lower() == "cash":
                value = bal.get("closing", bal.get("market_value"))
                return cls._money(value)
        return None

    def _compare_section(
        self, base: str, section: dict, account, rows: list
    ) -> None:
        """Compare one parsed currency section against each stored row."""
        cur = str(section.get("currency") or "CAD").upper()
        statement_date = section.get("statement_date")
        if statement_date is None:
            # No date means no row can be matched and the line format needs
            # a date token; the self-check has already flagged this.
            self._emit_note(f"NO_STATEMENT_DATE {base} {cur}")
            return

        prefix = f"{base} {account.name} {cur} {statement_date.isoformat()}"
        pdf_holdings = section.get("holdings") or []

        if not rows:
            # n == 0: every PDF field line is ONLY_PDF.
            self._emit_only_pdf_lines(prefix, section)
            return

        for row in rows:
            row_prefix = f"{prefix} [row {row.pk}]"

            # Scalar fields.
            self._emit_field(
                row_prefix, "last_statement_date",
                "MATCH"
                if section.get("last_statement_date") == row.last_statement_date
                else "DIFFER",
            )
            for field in ("begin_total_market_value", "end_total_market_value"):
                self._emit_field(
                    row_prefix, field,
                    self._value_verdict(self._money(section.get(field)),
                                        self._money(getattr(row, field))),
                )

            # Cash: presence-based (ONLY_PDF / ONLY_DB), then to the cent.
            pdf_cash = self._pdf_cash(section)
            stored_cash = row.balances.filter(account_type="Cash").first()
            if pdf_cash is None and stored_cash is None:
                verdict = "MATCH"
            elif pdf_cash is None:
                verdict = "ONLY_DB"
            elif stored_cash is None:
                verdict = "ONLY_PDF"
            else:
                verdict = self._value_verdict(pdf_cash, self._money(stored_cash.closing_balance))
            self._emit_field(row_prefix, "cash", verdict)

            # Number of holdings.
            db_count = row.holdings.count()
            self._emit_field(
                row_prefix, "holdings_count",
                "MATCH" if len(pdf_holdings) == db_count else "DIFFER",
            )

            # Per-symbol holding fields.
            self._compare_holdings(row_prefix, pdf_holdings, row)

    def _emit_only_pdf_lines(self, prefix: str, section: dict) -> None:
        """No stored row: emit ONLY_PDF for every field present in the PDF."""
        if section.get("last_statement_date") is not None:
            self._emit_field(prefix, "last_statement_date", "ONLY_PDF")
        for field in ("begin_total_market_value", "end_total_market_value"):
            self._emit_field(prefix, field, "ONLY_PDF")
        if self._pdf_cash(section) is not None:
            self._emit_field(prefix, "cash", "ONLY_PDF")

        pdf_holdings = section.get("holdings") or []
        if not pdf_holdings:
            return
        self._emit_field(prefix, "holdings_count", "ONLY_PDF")

        pdf_by_symbol: dict[str, list] = {}
        for h in pdf_holdings:
            pdf_by_symbol.setdefault(str(h.get("symbol") or ""), []).append(h)
        for sym in sorted(pdf_by_symbol):
            if len(pdf_by_symbol[sym]) > 1:
                self._emit_note(
                    f"{prefix} DUP_SYMBOLS {self._sym_token(sym)} "
                    f"{len(pdf_by_symbol[sym])} 0"
                )
            for field in ("quantity", "book_cost", "market_price", "market_value"):
                self._emit_field(
                    prefix, f"holding {self._sym_token(sym)} {field}", "ONLY_PDF"
                )

    def _value_verdict(self, pdf_val, db_val) -> str:
        """Both absent → MATCH; one absent or unequal → DIFFER."""
        if pdf_val is None and db_val is None:
            return "MATCH"
        if pdf_val is None or db_val is None:
            return "DIFFER"
        return "MATCH" if pdf_val == db_val else "DIFFER"

    def _compare_holdings(self, row_prefix: str, pdf_holdings: list, row) -> None:
        """Match holdings by symbol; duplicates in natural order (Q3)."""
        pdf_by_symbol: dict[str, list] = {}
        for h in pdf_holdings:
            pdf_by_symbol.setdefault(str(h.get("symbol") or ""), []).append(h)
        db_by_symbol: dict[str, list] = {}
        for h in row.holdings.all():  # Meta ordering: symbol
            db_by_symbol.setdefault(h.symbol or "", []).append(h)

        holding_fields = (
            ("quantity", lambda p: self._four_dp(p.get("quantity")),
             lambda d: self._four_dp(d.quantity)),
            ("book_cost", lambda p: self._money(p.get("book_cost")),
             lambda d: self._money(d.book_cost)),
            ("market_price", lambda p: self._four_dp(p.get("market_price")),
             lambda d: self._four_dp(d.market_price)),
            ("market_value", lambda p: self._money(p.get("market_value")),
             lambda d: self._money(d.market_value)),
        )

        for sym in sorted(set(pdf_by_symbol) | set(db_by_symbol)):
            pdf_list = pdf_by_symbol.get(sym, [])
            db_list = db_by_symbol.get(sym, [])
            if len(pdf_list) > 1 or len(db_list) > 1:
                self._emit_note(
                    f"{row_prefix} DUP_SYMBOLS {self._sym_token(sym)} "
                    f"{len(pdf_list)} {len(db_list)}"
                )
            for i in range(max(len(pdf_list), len(db_list))):
                pdf_h = pdf_list[i] if i < len(pdf_list) else None
                db_h = db_list[i] if i < len(db_list) else None
                for field, get_pdf, get_db in holding_fields:
                    label = f"holding {self._sym_token(sym)} {field}"
                    if pdf_h is None and db_h is None:
                        continue
                    if pdf_h is None:
                        verdict = "ONLY_DB"
                    elif db_h is None:
                        verdict = "ONLY_PDF"
                    else:
                        verdict = self._value_verdict(get_pdf(pdf_h), get_db(db_h))
                    self._emit_field(row_prefix, label, verdict)

    @staticmethod
    def _sym_token(sym: str) -> str:
        return sym if sym else "(blank)"
