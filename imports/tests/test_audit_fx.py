from django.test import TestCase
from django.core.management import call_command
from io import StringIO
from decimal import Decimal
from core.models import Transaction, Account, BrokerStatement

class AuditFxCommandTests(TestCase):
    def setUp(self):
        # Create test data
        self.account = Account.objects.create(
            account_id='AUDIT001',
            name='Test Account',
            broker='IB'
        )
        self.broker_statement = BrokerStatement.objects.create(
            source='IB',
            file_date='2023-01-01',
            raw_filename='test.csv'
        )

        # Create test transactions
        self.tx_ok = Transaction.objects.create(
            account=self.account,
            statement=self.broker_statement,
            date='2023-01-01',
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('10'),
            price=Decimal('150'),
            amount=Decimal('-1500'),
            currency='USD',
            exchange_rate_cad=Decimal('1.35'),
            amount_cad=Decimal('-2025.00'),
            commission=Decimal('0'),
            tx_hash='tx_ok_hash'
        )

        self.tx_bad = Transaction.objects.create(
            account=self.account,
            statement=self.broker_statement,
            date='2023-01-01',
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('10'),
            price=Decimal('100'),
            amount=Decimal('-1000'),
            currency='USD',
            exchange_rate_cad=Decimal('1.30'),
            amount_cad=Decimal('0.00'),
            commission=Decimal('0'),
            tx_hash='tx_bad_hash'
        )

        self.tx_zero = Transaction.objects.create(
            account=self.account,
            statement=self.broker_statement,
            date='2023-01-01',
            symbol='AAPL',
            type='DIV',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('0'),
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            amount_cad=Decimal('0.00'),
            commission=Decimal('0'),
            tx_hash='tx_zero_hash'
        )

    def test_audit_dry_run(self):
        out = StringIO()
        call_command('audit_fx', stdout=out)
        
        # Check output
        self.assertIn('Suspicious (amount_cad = 0, amount ≠ 0): 1', out.getvalue())
        
        # Check DB state
        self.tx_bad.refresh_from_db()
        self.assertEqual(self.tx_bad.amount_cad, Decimal('0.00'))

    def test_audit_fix(self):
        out = StringIO()
        call_command('audit_fx', fix=True, stdout=out)
        
        # Check DB state
        self.tx_bad.refresh_from_db()
        self.assertEqual(self.tx_bad.amount_cad, Decimal('-1300.00'))
        
        # Check output
        self.assertIn('Repaired: 1', out.getvalue())

    def test_audit_zero_amount_not_flagged(self):
        out = StringIO()
        call_command('audit_fx', stdout=out)
        
        # Check output - tx_zero (id=3) should not be listed
        output = out.getvalue()
        self.assertIn('Suspicious', output)  # tx_bad should be flagged
        self.assertNotIn(f'id={self.tx_zero.id}', output)  # but tx_zero should not be
        
    def test_audit_account_filter(self):
        # Create second account with bad transaction
        account2 = Account.objects.create(
            account_id='AUDIT002',
            name='Second Account',
            broker='RBC'
        )
        broker_statement2 = BrokerStatement.objects.create(
            source='RBC',
            file_date='2023-01-01',
            raw_filename='test2.csv'
        )
        tx_bad2 = Transaction.objects.create(
            account=account2,
            statement=broker_statement2,
            date='2023-01-01',
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('10'),
            price=Decimal('100'),
            amount=Decimal('-1000'),
            currency='USD',
            exchange_rate_cad=Decimal('1.30'),
            amount_cad=Decimal('0.00'),
            commission=Decimal('0'),
            tx_hash='tx_bad2_hash'
        )

        out = StringIO()
        call_command('audit_fx', account=self.account.account_id, stdout=out)
        
        self.assertIn('Suspicious (amount_cad = 0, amount ≠ 0): 1', out.getvalue())
        self.assertNotIn('Suspicious (amount_cad = 0, amount ≠ 0): 2', out.getvalue())