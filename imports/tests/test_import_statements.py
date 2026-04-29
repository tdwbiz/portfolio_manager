"""Tests for import_statements management command."""

import csv
import io
import os
import tempfile
from decimal import Decimal
from typing import Any, Dict, List

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from core.models import BrokerStatement, Transaction, Account


class ImportStatementsCommandTest(TestCase):
    """Tests for the import_statements management command."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.ib_csv_content: str = (
            "Trades\n"
            "Date/Time,Symbol,Buy/Sell,Quantity,T. Price,Comm/Fee,Proceeds,Currency\n"
            "2024-01-15 10:30:00,AAPL,BUY,10,150.50,2.99,1505.00,USD\n"
            "2024-01-16 11:00:00,GOOGL,SELL,5,2800.00,2.99,14000.00,USD\n"
            "2024-01-17 09:15:00,MSFT,BUY,20,300.25,2.99,6005.00,USD\n"
            "2024-01-18 14:45:00,AMZN,SELL,8,3300.00,2.99,26400.00,USD\n"
            "2024-01-19 10:00:00,TSLA,BUY,15,250.00,2.99,3750.00,USD\n"
        )
        
        # Real RBC Activity Export format — quoted CSV, exchange rate header,
        # "Month DD, YYYY" dates, commission hidden in price×qty vs value diff
        self.rbc_csv_content: str = (
            '"Activity Export as of January 15, 2024"\n'
            '\n'
            '"Exchange Rate: 1 USD = 1.3500 CAD"\n'
            '\n'
            '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
            '"Settlement Date","Account","Value","Currency","Description"\n'
            '"January 15, 2024","Buy","AAPL","Apple Inc.","10","150.50",'
            '"January 17, 2024","123456","1495.05","USD","Buy AAPL"\n'
            '"January 16, 2024","Sell","GOOGL","Alphabet Inc.","5","2800.00",'
            '"January 18, 2024","123456","13990.05","USD","Sell GOOGL"\n'
            '"January 17, 2024","Buy","MSFT","Microsoft Corp.","20","300.25",'
            '"January 19, 2024","123456","5995.05","USD","Buy MSFT"\n'
            '"January 18, 2024","Sell","AMZN","Amazon.com Inc.","8","3300.00",'
            '"January 20, 2024","123456","26390.05","USD","Sell AMZN"\n'
            '"January 19, 2024","Buy","TSLA","Tesla Inc.","15","250.00",'
            '"January 21, 2024","123456","3740.05","USD","Buy TSLA"\n'
        )

    def _create_temp_csv(self, content: str) -> str:
        """Create a temporary CSV file with the given content."""
        temp_file: tempfile.NamedTemporaryFile = tempfile.NamedTemporaryFile(
            mode='w', suffix='.csv', delete=False, newline=''
        )
        temp_file.write(content)
        temp_file.close()
        return temp_file.name

    def test_ib_import(self) -> None:
        """Test IB CSV import."""
        file_path: str = self._create_temp_csv(self.ib_csv_content)
        
        try:
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            
            # Check that BrokerStatement was created
            statements: BrokerStatement = BrokerStatement.objects.filter(source='IB')
            self.assertEqual(statements.count(), 1)
            
            # Check that transactions were created
            transactions: Transaction = Transaction.objects.filter(statement=statements.first())
            self.assertEqual(transactions.count(), 5)
            
            # Verify first transaction (AAPL)
            first_transaction: Transaction = transactions.first()
            self.assertEqual(first_transaction.symbol, 'AAPL')
            self.assertEqual(first_transaction.type, 'BUY')
            self.assertEqual(first_transaction.quantity, Decimal('10.0000'))
            self.assertEqual(first_transaction.price, Decimal('150.5000'))
            self.assertEqual(first_transaction.commission, Decimal('2.9900'))
            self.assertEqual(first_transaction.amount, Decimal('1505.00'))
            self.assertEqual(first_transaction.currency, 'USD')
            self.assertEqual(first_transaction.exchange_rate_cad, Decimal('1.000000'))
            self.assertEqual(first_transaction.amount_cad, Decimal('1505.00'))
        finally:
            os.unlink(file_path)

    def test_rbc_import(self) -> None:
        """Test RBC CSV import."""
        file_path: str = self._create_temp_csv(self.rbc_csv_content)
        
        try:
            call_command('import_statements', '--broker', 'RBC', '--file', file_path)
            
            # Check that BrokerStatement was created
            statements: BrokerStatement = BrokerStatement.objects.filter(source='RBC')
            self.assertEqual(statements.count(), 1)
            
            # Check that transactions were created
            transactions: Transaction = Transaction.objects.filter(statement=statements.first())
            self.assertEqual(transactions.count(), 5)
            
            # Verify first transaction (AAPL)
            # commission = abs(10 * 150.50 - 1495.05) = 9.95
            # amount_cad = 1495.05 * 1.35 = 2018.3175 → 2018.32
            first_transaction: Transaction = transactions.first()
            self.assertEqual(first_transaction.symbol, 'AAPL')
            self.assertEqual(first_transaction.type, 'BUY')
            self.assertEqual(first_transaction.quantity, Decimal('10.0000'))
            self.assertEqual(first_transaction.price, Decimal('150.5000'))
            self.assertEqual(first_transaction.commission, Decimal('9.95'))
            self.assertEqual(first_transaction.amount, Decimal('1495.05'))
            self.assertEqual(first_transaction.currency, 'USD')
            self.assertEqual(first_transaction.exchange_rate_cad, Decimal('1.350000'))
            self.assertEqual(first_transaction.amount_cad, Decimal('2018.32'))
        finally:
            os.unlink(file_path)

    def test_duplicate_detection(self) -> None:
        """Test duplicate detection - running same file twice should import 0 rows second time."""
        file_path: str = self._create_temp_csv(self.ib_csv_content)
        
        try:
            # First import
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            initial_count: int = Transaction.objects.count()
            self.assertEqual(initial_count, 5)
            
            # Second import (should skip all)
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            final_count: int = Transaction.objects.count()
            self.assertEqual(final_count, initial_count)  # No new transactions
            
            # Verify only one statement exists
            statements: BrokerStatement = BrokerStatement.objects.filter(source='IB')
            self.assertEqual(statements.count(), 1)
        finally:
            os.unlink(file_path)

    def test_dry_run(self) -> None:
        """Test --dry-run option - nothing should be written to DB."""
        file_path: str = self._create_temp_csv(self.ib_csv_content)
        
        try:
            call_command('import_statements', '--broker', 'IB', '--file', file_path, '--dry-run')
            
            # No statements or transactions should be created
            statements: BrokerStatement = BrokerStatement.objects.filter(source='IB')
            transactions: Transaction = Transaction.objects.filter(statement=statements.first())
            self.assertEqual(statements.count(), 0)
            self.assertEqual(transactions.count(), 0)
        finally:
            os.unlink(file_path)

    def test_file_not_found(self) -> None:
        """Test that CommandError is raised for non-existent file."""
        with self.assertRaises(CommandError) as context:
            call_command('import_statements', '--broker', 'IB', '--file', '/nonexistent/file.csv')
        
        self.assertIn("File not found", str(context.exception))

    def test_empty_file(self) -> None:
        """Test handling of empty CSV file."""
        empty_content: str = "Date/Time,Symbol,Buy/Sell,Quantity,T. Price,Comm/Fee,Proceeds,Currency\n"
        file_path: str = self._create_temp_csv(empty_content)
        
        try:
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            
            # No statements or transactions should be created
            statements: BrokerStatement = BrokerStatement.objects.filter(source='IB')
            self.assertEqual(statements.count(), 0)
        finally:
            os.unlink(file_path)

    def test_account_created_on_ib_import(self) -> None:
        """Test that Account is created when importing IB CSV with account_id."""
        ib_csv_with_account = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,TOM IB,AAPL stock,Buy,AAPL,"
            "10,150.25,USD,1502.50,1.00,1503.50\n"
        )
        file_path = self._create_temp_csv(ib_csv_with_account)
        try:
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            self.assertEqual(Account.objects.count(), 1)
            self.assertEqual(Account.objects.first().account_id, 'TOM IB')
        finally:
            os.unlink(file_path)

    def test_account_created_on_rbc_import(self) -> None:
        """Test that Account is created when importing RBC CSV with account_id."""
        rbc_csv_with_account = (
            '"Activity Export as of January 15, 2024"\n'
            '"Exchange Rate: 1 USD = 1.3500 CAD"\n'
            '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
            '"Settlement Date","Account","Value","Currency","Description"\n'
            '"January 15, 2024","Buy","AAPL","Apple Inc.","10","150.50",'
            '"January 17, 2024","TOM IB","1495.05","USD","Buy AAPL"\n'
        )
        file_path = self._create_temp_csv(rbc_csv_with_account)
        try:
            call_command('import_statements', '--broker', 'RBC', '--file', file_path)
            self.assertEqual(Account.objects.count(), 1)
            self.assertEqual(Account.objects.first().account_id, 'TOM IB')
        finally:
            os.unlink(file_path)

    def test_transaction_linked_to_account(self) -> None:
        """Test that Transaction is linked to Account after import."""
        ib_csv_with_account = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,SUSAN IB,AAPL stock,Buy,AAPL,"
            "10,150.25,USD,1502.50,1.00,1503.50\n"
        )
        file_path = self._create_temp_csv(ib_csv_with_account)
        try:
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            tx = Transaction.objects.first()
            self.assertIsNotNone(tx.account)
            self.assertEqual(tx.account.account_id, 'SUSAN IB')
        finally:
            os.unlink(file_path)

    def test_duplicate_tx_same_account_skipped(self) -> None:
        """Test that duplicate transaction for same account is skipped."""
        ib_csv = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,TOM IB,AAPL stock,Buy,AAPL,"
            "10,150.25,USD,1502.50,1.00,1503.50\n"
        )
        file_path = self._create_temp_csv(ib_csv)
        try:
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            initial_count = Transaction.objects.count()
            
            call_command('import_statements', '--broker', 'IB', '--file', file_path)
            final_count = Transaction.objects.count()
            
            self.assertEqual(initial_count, final_count)
        finally:
            os.unlink(file_path)

    def test_same_trade_different_accounts_both_imported(self) -> None:
        """Test that same trade values for different accounts are both imported."""
        ib_csv_tom = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,TOM IB,AAPL stock,Buy,AAPL,"
            "10,150.25,USD,1502.50,1.00,1503.50\n"
        )
        ib_csv_susan = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,SUSAN IB,AAPL stock,Buy,AAPL,"
            "10,150.25,USD,1502.50,1.00,1503.50\n"
        )
        file_path_tom = self._create_temp_csv(ib_csv_tom)
        file_path_susan = self._create_temp_csv(ib_csv_susan)
        try:
            call_command('import_statements', '--broker', 'IB', '--file', file_path_tom)
            call_command('import_statements', '--broker', 'IB', '--file', file_path_susan)
            
            self.assertEqual(Transaction.objects.count(), 2)
            self.assertEqual(Account.objects.count(), 2)
        finally:
            os.unlink(file_path_tom)
            os.unlink(file_path_susan)
