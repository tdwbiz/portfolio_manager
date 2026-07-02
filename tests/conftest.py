"""
Pytest configuration and fixtures for Playwright E2E tests.
"""
import os
import sys
import django
import pytest
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configure Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')
django.setup()

from django.contrib.auth import get_user_model
from django.test import Client


@pytest.fixture
def user(db):
    """Create a test user."""
    User = get_user_model()
    return User.objects.create_user(
        username='testuser',
        password='testpass123',
        email='test@example.com'
    )


@pytest.fixture
def authenticated_client(user):
    """Create a Django test client with authenticated user."""
    client = Client()
    client.login(username='testuser', password='testpass123')
    return client


@pytest.fixture
def django_db_setup(django_db_setup, django_db_blocker):
    """Ensure database is ready for tests."""
    with django_db_blocker.unblock():
        from django.core.management import call_command
        call_command('migrate', '--run-syncdb', verbosity=0)
