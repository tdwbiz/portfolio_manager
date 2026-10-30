"""
End-to-end tests for the exceptions page (phase 4, step 14c).
Playwright style of tests/e2e/test_portfolio.py; run with RUN_E2E=1 (tdw).

Plan: docs/plans/exceptions-page.md (Decisions: tdw, Oct 30).
"""

import re
from datetime import date

import pytest
from django.utils import timezone

from core.models import Account, MonthlyCheckResult, MonthlyException
from core.utils.account_fingerprint import fingerprint_account_number


class TestExceptionsPage:
    """Exceptions page user flows."""

    def _login(self, page, test_user):
        page.goto("/login/")
        page.fill('input[name="username"]', test_user.username)
        page.fill('input[name="password"]', "e2e-pass-123")
        page.click('button[type="submit"]')
        page.wait_for_url("/")

    @pytest.mark.django_db
    def test_navbar_link_and_list_loads(self, page, test_user):
        """The navbar link opens the exceptions list."""
        self._login(page, test_user)
        page.click("a.nav-link:has-text('Exceptions')")
        page.wait_for_url("**/exceptions/")
        assert "/exceptions/" in page.url

    @pytest.mark.django_db
    def test_list_filter_detail_and_accept(self, page, test_user):
        """One filter, the detail page, and accept with a reason."""
        account = Account.objects.create(
            name="ACCOUNT_A",
            fingerprint=fingerprint_account_number("90000001"),
            last_4="0001",
            broker="RBC",
        )
        now = timezone.now()
        MonthlyException.objects.create(
            kind="CASH",
            account=account,
            statement_date=date(2026, 1, 31),
            currency="CAD",
            ref="",
            detail="CASH: [CAD] expected 1.00, got 2.00, diff -1.00",
            status="open",
            first_seen=now,
            last_seen=now,
        )
        MonthlyCheckResult.objects.create(
            account=account,
            statement_date=date(2026, 1, 31),
            currency="CAD",
            verdict="FAIL",
            checked_at=now,
        )

        self._login(page, test_user)
        page.goto("/exceptions/")

        # One filter: kind = CASH.
        page.select_option("#id_kind", "CASH")
        page.click('form button:has-text("Filter")')
        assert page.locator("td", has_text="ACCOUNT_A").count() >= 1

        # Open the detail from the row link; stored detail is verbatim.
        page.click("a[href^='/exceptions/']:has-text('ACCOUNT_A')")
        page.wait_for_url(re.compile(r"/exceptions/\d+/$"))
        assert "CASH: [CAD] expected 1.00, got 2.00, diff -1.00" in page.content()

        # Accept with a reason.
        page.fill("#id_reason", "e2e known difference")
        page.click('button[type="submit"]:has-text("Accept")')
        page.wait_for_url(re.compile(r"/exceptions/\d+/$"))
        assert (
            page.locator(".badge.bg-warning:has-text('accepted')").count() == 1
        )
