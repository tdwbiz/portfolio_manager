"""
Pytest configuration and fixtures for Playwright E2E tests.
"""

import os
import sys
from pathlib import Path

# Browser tests need Playwright; only collected when RUN_E2E=1 (tdw only, never Pi).
collect_ignore = [] if os.environ.get("RUN_E2E") == "1" else ["e2e"]

import django
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

# Add project root to path
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configure Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings_test")
django.setup()


@pytest.fixture
def user(db):
    """Create a test user."""
    User = get_user_model()
    return User.objects.create_user(
        username="testuser", password="testpass123", email="test@example.com"
    )


@pytest.fixture
def authenticated_client(user):
    """Create a Django test client with authenticated user."""
    client = Client()
    client.login(username="testuser", password="testpass123")
    return client
