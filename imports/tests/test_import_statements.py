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

from core.models import BrokerStatement, Transaction


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
        
        self.rbc_csv_content: str = (
            "Date,Symbol,Description,Type,Quantity,Price,Commission,Total,Currency\n"
            "2024-01-15,AAPL,Apple Inc,BUY,10,150.50,2.99,1505.00,USD\n"
            "2024-01-16,GOOGL,Alphabet Inc,SELL,5,2800.00,2.99,14000.00,USD\n"
            "2024-01-17,MSFT,Microsoft Corp,BUY,20,300.25,2.99,6005.00,USD\n"
            "2024-01-18,AMZN,Amazon.com Inc,SELL,8,3300.00,2.99,26400.00,USD\n"
            "2024-01-19,TSLA,Tesla Inc,BUY,15,250.00,2.99,3750.00,USD\n"
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
            
            # Verify first transaction
            first_transaction: Transaction = transactions.first()
            self.assertEqual(first_transaction.symbol, 'AAPL')
            self.assertEqual(first_transaction.type, 'BUY')
            self.assertEqual(first_transaction.quantity, Decimal('10.0000'))
            self.assertEqual(first_transaction.price, Decimal('150.5000'))
            self.assertEqual(first_transaction.commission, Decimal('2.9900'))
            self.assertEqual(first_transaction.amount, Decimal('1505.00'))
            self.assertEqual(first_transaction.currency, 'USD')
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
            
            # Verify first transaction
            first_transaction: Transaction = transactions.first()
            self.assertEqual(first_transaction.symbol, 'AAPL')
            self.assertEqual(first_transaction.type, 'BUY')
            self.assertEqual(first_transaction.quantity, Decimal('10.0000'))
            self.assertEqual(first_transaction.price, Decimal('150.5000'))
            self.assertEqual(first_transaction.commission, Decimal('2.9900'))
            self.assertEqual(first_transaction.amount, Decimal('1505.00'))
            self.assertEqual(first_transaction.currency, 'USD')
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
