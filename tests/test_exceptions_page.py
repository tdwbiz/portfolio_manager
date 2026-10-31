"""Tests for the exceptions page (phase 4, step 14c). Synthetic data only.

Fake accounts 90000001-90000008 (ACCOUNT_A ... ACCOUNT_H) per AGENTS.md;
refs use letter-containing fake hash8 values so no rendered page can carry
a run of 8 digits. Plan: docs/plans/exceptions-page.md (Decisions: tdw,
Oct 30).
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from core.models import Account, MonthlyCheckResult, MonthlyException
from core.utils.account_fingerprint import fingerprint_account_number

LIST_URL = "/exceptions/"
COVERAGE_URL = "/exceptions/coverage/"


def make_account(name, number, category="TFSA"):
    return Account.objects.create(
        name=name,
        fingerprint=fingerprint_account_number(number),
        last_4=number[-4:],
        broker="RBC",
        category=category,
    )


def make_result(account, d, currency="CAD", verdict="PASS"):
    return MonthlyCheckResult.objects.create(
        account=account,
        statement_date=d,
        currency=currency,
        verdict=verdict,
        checked_at=timezone.now(),
    )


def make_exc(account, d, kind="CASH", status="open", currency="", ref="",
             detail=""):
    now = timezone.now()
    return MonthlyException.objects.create(
        kind=kind,
        account=account,
        statement_date=d,
        currency=currency,
        ref=ref,
        difference=None,
        detail=detail,
        status=status,
        first_seen=now,
        last_seen=now,
    )


def html(response):
    return response.content.decode()


def messages_in(response):
    # The POST redirects to the detail page, which renders the messages.
    return [str(m) for m in response.context["messages"]]


@pytest.mark.django_db
def test_login_required_on_all_three_urls():
    client = Client()
    for url in (LIST_URL, COVERAGE_URL, "/exceptions/1/"):
        response = client.get(url)
        assert response.status_code == 302
        assert response.url.startswith("/login/")


@pytest.mark.django_db
def test_list_ordering_status_then_taxable_then_name_date_kind(
    authenticated_client
):
    a = make_account("ACCOUNT_A", "90000001", category="TAXABLE")
    b = make_account("ACCOUNT_B", "90000002")
    c = make_account("ACCOUNT_C", "90000003", category="TAXABLE")
    d = date(2026, 1, 31)
    open_b = make_exc(b, d)
    open_a = make_exc(a, d)
    accepted_c = make_exc(c, d, status="accepted")
    accepted_b = make_exc(b, d, kind="CHAIN", status="accepted")
    resolved_a = make_exc(a, d, kind="QUANTITY", status="resolved")

    response = authenticated_client.get(LIST_URL)
    pks = [e.pk for e in response.context["exceptions"]]
    assert pks == [open_a.pk, open_b.pk, accepted_c.pk, accepted_b.pk,
                   resolved_a.pk]


@pytest.mark.django_db
def test_list_ordering_name_then_date_then_kind(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    b = make_account("ACCOUNT_B", "90000002")
    jan_cash = make_exc(a, date(2026, 1, 31))
    jan_chain = make_exc(a, date(2026, 1, 31), kind="CHAIN")
    feb_cash = make_exc(a, date(2026, 2, 28))
    b_jan = make_exc(b, date(2026, 1, 31))

    response = authenticated_client.get(LIST_URL)
    pks = [e.pk for e in response.context["exceptions"]]
    # Account name first (A before B), then statement_date, then kind.
    assert pks == [jan_cash.pk, jan_chain.pk, feb_cash.pk, b_jan.pk]


@pytest.mark.django_db
def test_list_filters(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    b = make_account("ACCOUNT_B", "90000002")
    e_a_cash = make_exc(a, date(2026, 1, 31))
    e_a_chain = make_exc(a, date(2026, 1, 31), kind="CHAIN")
    e_b_open = make_exc(b, date(2026, 2, 28))
    e_b_accepted = make_exc(b, date(2025, 12, 31), status="accepted")

    def pks(response):
        return {e.pk for e in response.context["exceptions"]}

    assert pks(authenticated_client.get(LIST_URL, {"account": a.pk})) == {
        e_a_cash.pk, e_a_chain.pk
    }
    assert pks(authenticated_client.get(LIST_URL, {"kind": "CASH"})) == {
        e_a_cash.pk, e_b_open.pk, e_b_accepted.pk
    }
    assert pks(authenticated_client.get(LIST_URL, {"status": "open"})) == {
        e_a_cash.pk, e_a_chain.pk, e_b_open.pk
    }
    assert pks(authenticated_client.get(LIST_URL, {"year": "2026"})) == {
        e_a_cash.pk, e_a_chain.pk, e_b_open.pk
    }
    # Month filter: statement date's year and month.
    assert pks(authenticated_client.get(LIST_URL, {"month": "2025-12"})) == {
        e_b_accepted.pk
    }
    assert pks(authenticated_client.get(LIST_URL, {"month": "2026-01"})) == {
        e_a_cash.pk, e_a_chain.pk
    }
    # Invalid values are ignored (no filtering).
    assert len(pks(authenticated_client.get(LIST_URL, {"account": "999"}))) == 4
    for bad_month in ("not-a-month", "2026-13", "2026-1", "202601"):
        assert len(pks(authenticated_client.get(LIST_URL, {"month": bad_month}))) == 4


@pytest.mark.django_db
def test_taxable_badge_in_list(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001", category="TAXABLE")
    b = make_account("ACCOUNT_B", "90000002")
    make_exc(a, date(2026, 1, 31))
    make_exc(b, date(2026, 1, 31))

    response = authenticated_client.get(LIST_URL)
    body = html(response)
    assert body.count("taxable</span>") == 1  # only ACCOUNT_A's row
    row_a = re.search(r"ACCOUNT_A.*?taxable", body, re.S)
    assert row_a is not None


@pytest.mark.django_db
def test_status_counts_over_filtered_queryset(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    b = make_account("ACCOUNT_B", "90000002")
    make_exc(a, date(2026, 1, 31))
    make_exc(a, date(2026, 2, 28), kind="CHAIN")
    make_exc(b, date(2026, 1, 31), status="accepted")
    make_exc(b, date(2026, 2, 28), status="resolved")

    response = authenticated_client.get(LIST_URL)
    assert response.context["status_counts"] == {
        "open": 2, "accepted": 1, "resolved": 1
    }
    response = authenticated_client.get(LIST_URL, {"account": a.pk})
    assert response.context["status_counts"] == {
        "open": 2, "accepted": 0, "resolved": 0
    }
    # Counts follow every filter except status: with status=open selected the
    # accepted/resolved badges still show their real numbers.
    response = authenticated_client.get(LIST_URL, {"status": "open"})
    assert response.context["status_counts"] == {
        "open": 2, "accepted": 1, "resolved": 1
    }
    # ...and the list itself is still filtered by status.
    assert [e.status for e in response.context["exceptions"]] == [
        "open", "open"
    ]


@pytest.mark.django_db
def test_detail_shows_stored_detail_verbatim(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    detail = (
        "CASH: [CAD] expected 100.00, got 99.50, diff -0.50\n"
        "<script>alert(1)</script>\n"
        "UNMATCHED CSV ROW: ab12cd34"
    )
    exc = make_exc(a, date(2026, 1, 31), detail=detail)

    response = authenticated_client.get(f"/exceptions/{exc.pk}/")
    body = html(response)
    # Autoescape on: the script tag is escaped, plain lines verbatim.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert" not in body
    assert "CASH: [CAD] expected 100.00, got 99.50, diff -0.50" in body
    assert "UNMATCHED CSV ROW: ab12cd34" in body


@pytest.mark.django_db
def test_accept_without_reason_refused(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    exc = make_exc(a, date(2026, 1, 31))

    response = authenticated_client.post(
        f"/exceptions/{exc.pk}/", {"action": "accept", "reason": "   "}
    )
    assert response.status_code == 302
    exc.refresh_from_db()
    assert exc.status == "open"
    assert exc.reason == ""
    assert exc.accepted_at is None


@pytest.mark.django_db
def test_accept_with_reason_sets_status_reason_accepted_at(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    exc = make_exc(a, date(2026, 1, 31))

    response = authenticated_client.post(
        f"/exceptions/{exc.pk}/", {"action": "accept", "reason": "known diff"}
    )
    assert response.status_code == 302
    exc.refresh_from_db()
    assert exc.status == "accepted"
    assert exc.reason == "known diff"
    assert exc.accepted_at is not None


@pytest.mark.django_db
def test_success_messages_read_accepted_and_reopened(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    exc = make_exc(a, date(2026, 1, 31))

    response = authenticated_client.post(
        f"/exceptions/{exc.pk}/",
        {"action": "accept", "reason": "known diff"},
        follow=True,
    )
    assert messages_in(response) == ["Exception accepted: known diff"]

    exc.refresh_from_db()
    response = authenticated_client.post(
        f"/exceptions/{exc.pk}/",
        {"action": "reopen", "reason": "diff changed"},
        follow=True,
    )
    assert messages_in(response) == ["Exception reopened: diff changed"]


@pytest.mark.django_db
def test_reopen_resets_status_and_appends_note(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    exc = make_exc(a, date(2026, 1, 31), status="accepted", detail="line1")
    exc.accepted_at = timezone.now()
    exc.save()

    response = authenticated_client.post(
        f"/exceptions/{exc.pk}/", {"action": "reopen", "reason": "diff changed"}
    )
    assert response.status_code == 302
    exc.refresh_from_db()
    assert exc.status == "open"
    assert exc.accepted_at is None
    assert exc.resolved_at is None
    today = timezone.now().strftime("%Y-%m-%d")
    assert exc.detail == f"line1\n[reopened {today}: diff changed]"


@pytest.mark.django_db
def test_get_never_changes_anything(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    exc = make_exc(a, date(2026, 1, 31), status="accepted", detail="line1")
    before = (exc.status, exc.reason, exc.accepted_at, exc.detail)

    authenticated_client.get(LIST_URL)
    authenticated_client.get(COVERAGE_URL)
    response = authenticated_client.get(f"/exceptions/{exc.pk}/")
    assert response.status_code == 200

    exc.refresh_from_db()
    assert (exc.status, exc.reason, exc.accepted_at, exc.detail) == before


@pytest.mark.django_db
def test_accept_reopen_refused_on_wrong_status(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    accepted = make_exc(a, date(2026, 1, 31), status="accepted")
    open_exc = make_exc(a, date(2026, 2, 28))

    # Accept on an accepted row: refused.
    response = authenticated_client.post(
        f"/exceptions/{accepted.pk}/", {"action": "accept", "reason": "x"}
    )
    assert response.status_code == 302
    accepted.refresh_from_db()
    assert accepted.status == "accepted"

    # Reopen on an open row: refused.
    response = authenticated_client.post(
        f"/exceptions/{open_exc.pk}/", {"action": "reopen", "reason": "x"}
    )
    assert response.status_code == 302
    open_exc.refresh_from_db()
    assert open_exc.status == "open"
    assert "[reopened" not in open_exc.detail


@pytest.mark.django_db
def test_kind_renamed_to_exception_type(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    exc = make_exc(a, date(2026, 1, 31))

    list_body = html(authenticated_client.get(LIST_URL))
    # Column header, filter label and the dropdown's first option.
    assert "<th>Exception Type</th>" in list_body
    assert (
        '<label for="id_kind" class="form-label">Exception Type</label>'
        in list_body
    )
    assert "\u2014 All exception types \u2014" in list_body
    assert "All kinds" not in list_body
    # The GET parameter stays kind.
    assert 'name="kind"' in list_body

    detail_body = html(authenticated_client.get(f"/exceptions/{exc.pk}/"))
    assert "<tr><th>Exception Type</th>" in detail_body


@pytest.mark.django_db
def test_detail_unknown_id_is_404(authenticated_client):
    response = authenticated_client.get("/exceptions/99999/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_coverage_grid_cell_states(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001", category="TAXABLE")
    b = make_account("ACCOUNT_B", "90000002")

    # 2026-01-31: a combined FAIL (USD FAIL, no exceptions); b PASS with an
    # IMPORT marker.
    make_result(a, date(2026, 1, 31), "CAD", "PASS")
    make_result(a, date(2026, 1, 31), "USD", "FAIL")
    make_result(b, date(2026, 1, 31), "CAD", "PASS")
    make_exc(b, date(2026, 1, 31), kind="IMPORT", ref="ab12cd34")

    # 2026-02-28: a FAIL verdict with its only check exception accepted ->
    # ACCEPTED; b has no result -> gap.
    make_result(a, date(2026, 2, 28), "USD", "FAIL")
    make_exc(a, date(2026, 2, 28), currency="USD", status="accepted")

    # 2026-03-31: a FIRST (one currency), b NO_CSV (one currency).
    make_result(a, date(2026, 3, 31), "CAD", "FIRST")
    make_result(a, date(2026, 3, 31), "USD", "PASS")
    make_result(b, date(2026, 3, 31), "CAD", "NO_CSV")
    make_result(b, date(2026, 3, 31), "USD", "PASS")

    # 2026-04-30: a open QUANTITY (blank currency) marks the cell FAIL even
    # though both stored verdicts are PASS; b plain PASS.
    make_result(a, date(2026, 4, 30), "CAD", "PASS")
    make_result(a, date(2026, 4, 30), "USD", "PASS")
    make_exc(a, date(2026, 4, 30), kind="QUANTITY", ref="MSFT")
    make_result(b, date(2026, 4, 30), "CAD", "PASS")

    response = authenticated_client.get(COVERAGE_URL)
    rows = {row["account"].name: row for row in response.context["rows"]}

    # Taxable account first.
    assert [r["account"].name for r in response.context["rows"]] == [
        "ACCOUNT_A", "ACCOUNT_B"
    ]

    def cell(account, d):
        return next(
            c for c in rows[account]["cells"] if c["date"] == d
        )

    assert cell("ACCOUNT_A", date(2026, 1, 31))["state"] == "FAIL"
    assert cell("ACCOUNT_A", date(2026, 1, 31))["letters"] == "F"
    assert cell("ACCOUNT_B", date(2026, 1, 31))["state"] == "PASS"
    assert cell("ACCOUNT_B", date(2026, 1, 31))["letters"] == "P i"
    assert (
        cell("ACCOUNT_A", date(2026, 1, 31))["title"]
        == "2026-01-31 — CAD PASS, USD FAIL"
    )

    assert cell("ACCOUNT_A", date(2026, 2, 28))["state"] == "ACCEPTED"
    assert cell("ACCOUNT_A", date(2026, 2, 28))["letters"] == "A"
    assert cell("ACCOUNT_B", date(2026, 2, 28))["state"] == "GAP"
    assert cell("ACCOUNT_B", date(2026, 2, 28))["css_class"] == ""

    assert cell("ACCOUNT_A", date(2026, 3, 31))["state"] == "FIRST"
    assert cell("ACCOUNT_A", date(2026, 3, 31))["letters"] == "1"
    assert cell("ACCOUNT_B", date(2026, 3, 31))["state"] == "NO_CSV"

    # Open QUANTITY with blank currency drives the cell to FAIL.
    assert cell("ACCOUNT_A", date(2026, 4, 30))["state"] == "FAIL"
    assert cell("ACCOUNT_B", date(2026, 4, 30))["state"] == "PASS"

    # Cells link to the filtered list (account + month).
    body = html(response)
    assert f"{LIST_URL}?account={a.pk}&month=2026-01" in body


@pytest.mark.django_db
def test_coverage_resolved_exception_ignored_for_accepted(
    authenticated_client,
):
    a = make_account("ACCOUNT_A", "90000001")
    # Verdict FAIL with one accepted and one resolved check exception on the
    # same statement: the resolved one is ignored, so the cell is ACCEPTED.
    make_result(a, date(2026, 1, 31), "CAD", "FAIL")
    make_exc(a, date(2026, 1, 31), currency="CAD", status="accepted")
    make_exc(
        a, date(2026, 1, 31), kind="CHAIN", status="resolved"
    )

    response = authenticated_client.get(COVERAGE_URL)
    rows = {row["account"].name: row for row in response.context["rows"]}
    cell = next(
        c for c in rows["ACCOUNT_A"]["cells"] if c["date"] == date(2026, 1, 31)
    )
    assert cell["state"] == "ACCEPTED"
    assert cell["letters"] == "A"


@pytest.mark.django_db
def test_coverage_empty_when_no_results(authenticated_client):
    make_account("ACCOUNT_A", "90000001")
    response = authenticated_client.get(COVERAGE_URL)
    assert response.status_code == 200
    assert response.context["dates"] == []


@pytest.mark.django_db
def test_navbar_badge_count_for_logged_in_user(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001")
    make_exc(a, date(2026, 1, 31))
    make_exc(a, date(2026, 2, 28), kind="CHAIN")
    make_exc(a, date(2026, 3, 31), status="accepted")

    body = html(authenticated_client.get(LIST_URL))
    assert 'href="/exceptions/"' in body
    assert '<span class="badge bg-danger">2</span>' in body


@pytest.mark.django_db
def test_navbar_badge_absent_for_anonymous():
    make_account("ACCOUNT_A", "90000001")
    make_exc(make_account("ACCOUNT_B", "90000002"), date(2026, 1, 31))

    body = html(Client().get("/login/"))
    assert 'href="/exceptions/"' not in body


@pytest.mark.django_db
def test_no_run_of_8_digits_in_any_rendered_page(authenticated_client):
    a = make_account("ACCOUNT_A", "90000001", category="TAXABLE")
    exc = make_exc(
        a, date(2026, 1, 31), kind="IMPORT", ref="ab12cd34",
        detail="CASH: [CAD] expected 1.00, got 2.00, diff -1.00\n"
               "UNMATCHED PDF ROW ab12cd34",
    )
    make_result(a, date(2026, 1, 31), "CAD", "PASS")

    for url in (LIST_URL, COVERAGE_URL, f"/exceptions/{exc.pk}/"):
        body = html(authenticated_client.get(url))
        assert re.search(r"\d{8}", body) is None, url
