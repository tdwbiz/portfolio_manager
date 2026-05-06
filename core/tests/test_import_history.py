from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.conf import settings
from core.models import FileImportLog
from pathlib import Path
import tempfile
import shutil
import datetime


class ImportHistoryTests(TestCase):
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        # Create test FileImportLog objects
        FileImportLog.objects.create(
            filename='test1.csv',
            broker='IB',
            imported_at=datetime.datetime(2024, 1, 15, 10, 30),
            records_imported=100,
            records_skipped=5,
            status='SUCCESS',
            error_message=''
        )
        
        FileImportLog.objects.create(
            filename='test2.csv',
            broker='RBC',
            imported_at=datetime.datetime(2024, 2, 20, 14, 45),
            records_imported=50,
            records_skipped=2,
            status='SUCCESS',
            error_message=''
        )
        
        FileImportLog.objects.create(
            filename='test3.csv',
            broker='IB',
            imported_at=datetime.datetime(2024, 3, 10, 9, 15),
            records_imported=0,
            records_skipped=0,
            status='ERROR',
            error_message='This is a very long error message that should be truncated to 80 characters when displayed in the UI'
        )

    def test_import_history_loads(self):
        response = self.client.get(reverse('import_history'))
        self.assertEqual(response.status_code, 200)

    def test_import_history_shows_filename(self):
        response = self.client.get(reverse('import_history'))
        self.assertIn(b'test1.csv', response.content)
        self.assertIn(b'test2.csv', response.content)

    def test_rejection_log_download_invalid_month(self):
        response = self.client.get(reverse('download_rejection_log', args=['badmonth']))
        self.assertEqual(response.status_code, 400)

    def test_rejection_log_download_not_found(self):
        response = self.client.get(reverse('download_rejection_log', args=['2099_01']))
        self.assertEqual(response.status_code, 404)

    def test_rejection_log_download_ok(self):
        # Create a temporary directory for test
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Create a test rejection log file
            test_file = Path(temp_dir) / 'rejected_2024_01.csv'
            test_file.write_text('date,symbol,error\n2024-01-01,AAPL,test error\n')
            
            # Temporarily override settings
            with self.settings(IMPORT_LOGS_DIR=temp_dir):
                response = self.client.get(reverse('download_rejection_log', args=['2024_01']))
                
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get('Content-Type').startswith('text/csv'))
                self.assertIn('attachment', response.get('Content-Disposition'))
                
                # Close the response to release file handle
                response.close()
        finally:
            # Clean up - retry a few times if file is locked
            import time
            for _ in range(3):
                try:
                    shutil.rmtree(temp_dir)
                    break
                except PermissionError:
                    time.sleep(0.1)
