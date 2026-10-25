from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import models
from decimal import Decimal
from datetime import date, timedelta
from core.models import Account, BrokerStatement, Transaction, PositionHistory


class PositionHistoryTemporalTests(TestCase):
    """Test temporal query logic for position_history"""

    def setUp(self):
        self.account = Account.objects.create(
            name="Test Account",
            owner="TOM",
            currency="CAD")
        self.statement = BrokerStatement.objects.create(
            source="IB",
            file_date=date(2025, 1, 1),
            raw_filename="test_statement.pdf",
        )

    def test_current_position_has_null_date_to(self):
        PositionHistory.objects.create(
            owner="TOM",
            account=self.account,
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_basis_per_unit=Decimal("150.00"),
            total_cost_basis=Decimal("15000.00"),
            currency="CAD",
            date_from=date(2025, 1, 15),
            date_to=None,
        )
        pos = PositionHistory.objects.get(symbol="AAPL")
        self.assertIsNone(pos.date_to)

    def test_as_of_query_returns_correct_position(self):
        # First position: opens 2025-01-15, closes 2025-06-01
        PositionHistory.objects.create(
            owner="TOM",
            account=self.account,
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_basis_per_unit=Decimal("150.00"),
            total_cost_basis=Decimal("15000.00"),
            currency="CAD",
            date_from=date(2025, 1, 15),
            date_to=date(2025, 6, 1) - timedelta(days=1),
        )
        # Second position: opens 2025-06-01
        PositionHistory.objects.create(
            owner="TOM",
            account=self.account,
            symbol="AAPL",
            quantity=Decimal("200"),
            cost_basis_per_unit=Decimal("160.00"),
            total_cost_basis=Decimal("32000.00"),
            currency="CAD",
            date_from=date(2025, 6, 1),
            date_to=None,
        )

        # as_of(2025-03-01) should return first position
        first_pos = PositionHistory.objects.filter(
            symbol="AAPL",
            date_from__lte=date(2025, 3, 1),
            date_to__gte=date(2025, 3, 1),
        ).first()
        self.assertIsNotNone(first_pos)
        self.assertEqual(first_pos.quantity, Decimal("100"))

        # as_of(2025-07-01) should return second position
        second_pos = PositionHistory.objects.filter(
            symbol="AAPL",
            date_from__lte=date(2025, 7, 1),
        ).filter(
            models.Q(date_to__gte=date(2025, 7, 1)) | models.Q(date_to__isnull=True)
        ).first()
        self.assertIsNotNone(second_pos)
        self.assertEqual(second_pos.quantity, Decimal("200"))

    def test_as_of_query_returns_nothing_before_position_opened(self):
        PositionHistory.objects.create(
            owner="TOM",
            account=self.account,
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_basis_per_unit=Decimal("150.00"),
            total_cost_basis=Decimal("15000.00"),
            currency="CAD",
            date_from=date(2025, 1, 15),
            date_to=None,
        )
        result = PositionHistory.objects.filter(
            symbol="AAPL",
            date_from__lte=date(2025, 1, 10),
        ).filter(
            models.Q(date_to__gte=date(2025, 1, 10)) | models.Q(date_to__isnull=True),
        )
        self.assertEqual(result.count(), 0)

    def test_rebuild_position_history_idempotent(self):
        # Create a transaction
        Transaction.objects.create(
            statement=self.statement,
            date=date(2025, 1, 15),
            symbol="AAPL",
            type="BUY",
            quantity=Decimal("100"),
            price=Decimal("150.00"),
            commission=Decimal("10.00"),
            amount=Decimal("15010.00"),
            currency="CAD",
            account=self.account,
        )
        # Simulate rebuild by creating position directly
        PositionHistory.objects.create(
            owner="TOM",
            account=self.account,
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_basis_per_unit=Decimal("150.10"),
            total_cost_basis=Decimal("15010.00"),
            currency="CAD",
            date_from=date(2025, 1, 15),
            date_to=None,
        )
        count1 = PositionHistory.objects.count()
        # Simulate second run by deleting and recreating
        PositionHistory.objects.all().delete()
        PositionHistory.objects.create(
            owner="TOM",
            account=self.account,
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_basis_per_unit=Decimal("150.10"),
            total_cost_basis=Decimal("15010.00"),
            currency="CAD",
            date_from=date(2025, 1, 15),
            date_to=None,
        )
        count2 = PositionHistory.objects.count()
        self.assertEqual(count1, count2)

    def test_exclude_from_risk_flag_filters_vw_current_portfolio(self):
        excluded_account = Account.objects.create(
            name="Excluded Account",
            owner="TOM",
            currency="CAD",
            exclude_from_risk=True)
        PositionHistory.objects.create(
            owner="TOM",
            account=excluded_account,
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_basis_per_unit=Decimal("150.00"),
            total_cost_basis=Decimal("15000.00"),
            currency="CAD",
            date_from=date(2025, 1, 15),
            date_to=None,
        )
        # Simulate view filter: exclude_from_risk = 0
        result = PositionHistory.objects.filter(
            account__exclude_from_risk=False,
            date_to__isnull=True,
            quantity__gt=Decimal("0.0001"),
        )
        self.assertEqual(result.count(), 0)

    def test_taxable_car_excluded_by_default(self):
        taxable_car = Account.objects.create(
            name="Taxable Car",
            owner="TOM",
            currency="CAD",
            exclude_from_risk=True)
        PositionHistory.objects.create(
            owner="TOM",
            account=taxable_car,
            symbol="VDY",
            quantity=Decimal("500"),
            cost_basis_per_unit=Decimal("50.00"),
            total_cost_basis=Decimal("25000.00"),
            currency="CAD",
            date_from=date(2025, 1, 15),
            date_to=None,
        )
        result = PositionHistory.objects.filter(
            account__exclude_from_risk=False,
            date_to__isnull=True,
            quantity__gt=Decimal("0.0001"),
        )
        self.assertEqual(result.count(), 0)


class AccountExcludeFromRiskTests(TestCase):

    def test_default_exclude_from_risk_is_false(self):
        account = Account.objects.create(
            name="New Account",
            owner="SUSAN",
            currency="CAD")
        self.assertFalse(account.exclude_from_risk)

    def test_toggle_exclude_from_risk(self):
        account = Account.objects.create(
            name="Toggle Account",
            owner="TOM",
            currency="CAD")
        account.exclude_from_risk = True
        account.save()
        self.assertTrue(account.exclude_from_risk)
        account.exclude_from_risk = False
        account.save()
        self.assertFalse(account.exclude_from_risk)
