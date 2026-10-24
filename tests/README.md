# Portfolio Manager - Automated Testing Framework

This directory contains automated end-to-end (E2E) tests that simulate real user interactions with the portfolio_manager application using **Playwright** and **pytest**.

## 📋 Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Running Tests](#running-tests)
- [Test Suites](#test-suites)
- [Test Files](#test-files)
- [Writing New Tests](#writing-new-tests)
- [Continuous Integration](#continuous-integration)
- [Troubleshooting](#troubleshooting)

## 🎯 Overview

The E2E tests simulate real user scenarios including:

- ✅ User authentication (login/logout)
- ✅ Dashboard navigation
- ✅ Portfolio management
- ✅ PDF import workflows
- ✅ Form validation
- ✅ Responsive design
- ✅ Security features (CSRF, open redirect protection)
- ✅ Performance metrics
- ✅ API endpoint testing
- ✅ Chart rendering

## 📦 Installation

### Prerequisites

- Python 3.11+
- Django 5.1+
- Playwright browser binaries

### Setup

```bash
# Install testing dependencies
pip install -r requirements.txt

# Install Playwright browsers (chromium used by default)
playwright install chromium

# Run database migrations
python manage.py migrate --settings=portfolio_manager.settings_test
```

### Optional: Install pytest-html for reports

```bash
pip install pytest-html
```

## ▶️ Running Tests

> **Note:** the browser (Playwright) tests in `tests/e2e/` are only collected when
> `RUN_E2E=1` is set — they are tdw-only and never run on Pi's sandbox.
> Plain pytest runs (`python -m pytest tests/`) therefore skip them automatically.

### Quick Start

```bash
# Run all E2E tests (headless mode)
python run_e2e_tests.py

# Run with visible browser (headed mode)
python run_e2e_tests.py --headed

# List available test suites
python run_e2e_tests.py --list
```

### Run Specific Test Suite

```bash
# Authentication tests
python run_e2e_tests.py --suite auth

# Navigation tests
python run_e2e_tests.py --suite navigation

# Portfolio tests
python run_e2e_tests.py --suite portfolio

# Security tests
python run_e2e_tests.py --suite security

# Performance tests
python run_e2e_tests.py --suite performance

# All workflow tests
python run_e2e_tests.py --suite workflows
```

### Run Specific Test File

```bash
python run_e2e_tests.py --file tests/e2e/test_e2e_user_journey.py
```

### Generate HTML Report

```bash
python run_e2e_tests.py --report
# Report generated at: tests/test_report.html
```

### Direct pytest Commands

```bash
# Run all tests
python -m pytest tests/ -v

# Run with specific marker
python -m pytest tests/ -v -k "TestUserAuthentication"

# Run smoke tests only
python -m pytest tests/ -v -m smoke

# Run with headed browser
python -m pytest tests/ -v --headed

# Generate HTML report
python -m pytest tests/ -v --html=tests/test_report.html --self-contained-html

# Run with verbose output and no stop on first failure
python -m pytest tests/ -v --tb=long
```

## 📚 Test Suites

| Suite | Description | Tests |
|-------|-------------|-------|
| **auth** | User authentication flows | Login, logout, failed login, open redirect protection |
| **navigation** | Dashboard and navigation | Page loading, menu clicks, routing |
| **portfolio** | Portfolio management | Portfolio page, summary views |
| **import** | PDF import functionality | Import page, file upload forms |
| **responsive** | Responsive design | Multiple viewport sizes (mobile, tablet, desktop) |
| **forms** | Form validation | Empty submissions, error messages |
| **workflows** | Complete user journeys | Onboarding, account creation, transaction management |
| **security** | Security features | CSRF protection, session timeout, unauthorized access |
| **performance** | Page load metrics | Dashboard load time, account list load time |
| **api** | API endpoints & charts | AJAX calls, chart rendering, JavaScript errors |

## 📁 Test Files

### `tests/e2e/test_e2e_user_journey.py`
Core user journey tests:
- `TestUserAuthentication` - Login/logout flows
- `TestDashboardNavigation` - Navigation and routing
- `TestPortfolioFeatures` - Portfolio management
- `TestPDFImportFlow` - PDF import functionality
- `TestResponsiveDesign` - Responsive design across viewports
- `TestFormValidation` - Form validation and error handling

### `tests/e2e/test_user_workflows.py`
Comprehensive user workflow tests:
- `TestCompleteUserOnboarding` - First-time user experience
- `TestTransactionManagement` - Transaction creation and viewing
- `TestReportGeneration` - Report access and generation
- `TestSecurityFeatures` - Security feature validation
- `TestPerformanceMetrics` - Page load performance

### `tests/e2e/test_api_endpoints.py`
API and JavaScript interaction tests:
- `TestAPIEndpoints` - AJAX endpoint testing
- `TestChartRendering` - Chart visualization testing
- `TestRealTimeFeatures` - JavaScript error detection

## ✍️ Writing New Tests

### Basic Test Structure

```python
from django.test import StaticLiveServerTestCase
from django.contrib.auth import get_user_model

class TestNewFeature(StaticLiveServerTestCase):
    """Test new feature description."""
    
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        
        # Create test user
        cls.user = get_user_model().objects.create_user(
            username='test_user',
            password='test_pass_123',
            email='test@example.com'
        )
    
    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()
    
    def setUp(self):
        self.page = self.browser.new_page()
    
    def tearDown(self):
        self.page.close()
    
    def test_specific_scenario(self):
        """Test a specific user scenario."""
        # 1. Navigate to page
        self.page.get(f'{self.live_server_url}/core/your-page/')
        
        # 2. Wait for content
        self.page.wait_for_selector('.expected-element')
        
        # 3. Interact with page
        self.page.fill('input[name="field"]', 'test value')
        self.page.click('button[type="submit"]')
        
        # 4. Verify result
        self.page.wait_for_url('**/expected-url/**')
        assert 'expected' in self.page.url.lower()
```

### Best Practices

1. **Use `StaticLiveServerTestCase`** - Provides live Django server for testing
2. **Clean up in `tearDownClass`** - Delete test users and data
3. **Use helper methods** - Extract common actions (login, navigate)
4. **Add meaningful assertions** - Verify expected behavior
5. **Use appropriate waits** - `wait_for_selector`, `wait_for_url`, `wait_for_timeout`
6. **Mark slow tests** - Use `@pytest.mark.slow` for tests taking >5 seconds

### Common Playwright Patterns

```python
# Navigate to page
self.page.get(f'{self.live_server_url}/core/page/')

# Wait for element
self.page.wait_for_selector('.element-class')

# Fill form field
self.page.fill('input[name="username"]', 'testuser')

# Click button
self.page.click('button[type="submit"]')

# Wait for URL change
self.page.wait_for_url('**/dashboard/**')

# Take screenshot
self.page.screenshot(path='screenshot.png')

# Execute JavaScript
result = self.page.evaluate('() => document.title')

# Check for element existence
has_element = self.page.query_selector('.element') is not None

# Get page content
content = self.page.content()
```

## 🚀 Continuous Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        playwright install chromium
    
    - name: Run migrations
      run: |
        python manage.py migrate --settings=portfolio_manager.settings_test
    
    - name: Run E2E tests
      run: |
        python run_e2e_tests.py --report
    
    - name: Upload test report
      if: always()
      uses: actions/upload-artifact@v3
      with:
        name: test-report
        path: tests/test_report.html
```

## 🔧 Troubleshooting

### Common Issues

#### 1. Playwright browser not installed
```bash
playwright install chromium
```

#### 2. Database migration errors
```bash
python manage.py migrate --settings=portfolio_manager.settings_test
```

#### 3. Tests failing with timeout
- Increase timeout in tests: `self.page.wait_for_selector('.element', timeout=10000)`
- Check if Django server starts properly
- Verify port is not in use

#### 4. Browser crashes
- Run in headless mode (default)
- Update Playwright: `playwright install --force`
- Check system resources (memory/CPU)

#### 5. Authentication failures
- Verify test user is created in `setUpClass`
- Check login credentials match
- Ensure login URL is correct

### Debug Mode

```bash
# Run with headed browser to see what's happening
python run_e2e_tests.py --headed

# Add pauses in tests
self.page.wait_for_timeout(5000)  # Pause for 5 seconds

# Take screenshots on failure
self.page.screenshot(path=f'tests/screenshots/{test_name}.png')
```

### Logging

Tests output to console by default. For more detailed logging:

```bash
python -m pytest tests/ -v --log-cli-level=DEBUG
```

## 📊 Test Reports

### HTML Report

```bash
python run_e2e_tests.py --report
```

Opens `tests/test_report.html` in your browser with:
- Pass/fail summary
- Execution time
- Error details
- Screenshots (if configured)

### Console Output

```
tests/e2e/test_e2e_user_journey.py::TestUserAuthentication::test_login_page_loads PASSED
tests/e2e/test_e2e_user_journey.py::TestUserAuthentication::test_successful_login PASSED
tests/e2e/test_e2e_user_journey.py::TestUserAuthentication::test_failed_login PASSED
...
```

## 🎓 Learning Resources

- [Playwright Python Documentation](https://playwright.dev/python/)
- [pytest Documentation](https://docs.pytest.org/)
- [Django Testing Documentation](https://docs.djangoproject.com/en/stable/topics/testing/)
- [pytest-django Documentation](https://pytest-django.readthedocs.io/)

## 📝 Notes

- Tests use `portfolio_manager.settings_test` for database configuration
- All tests clean up after themselves (users, data)
- Tests run in headless mode by default
- Screenshots saved to `tests/screenshots/` directory
- Use `--headed` flag to see browser actions in real-time

## 🤝 Contributing

When adding new tests:

1. Follow existing test structure and patterns
2. Add appropriate docstrings
3. Include setup/teardown for test users
4. Add meaningful assertions
5. Update this README if adding new test suites
6. Run tests locally before committing

---

**Last Updated:** 2026-07-01  
**Framework:** Playwright + pytest + Django  
**Test Coverage:** User journeys, security, performance, API endpoints
