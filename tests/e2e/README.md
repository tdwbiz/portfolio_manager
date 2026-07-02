# End-to-End Testing with Playwright

This directory contains end-to-end (E2E) tests that simulate real user interactions with the portfolio_manager application using Playwright.

## Setup

The required packages are already installed in the `ai-env` environment:
- **PyTorch** (2.11.0+cpu) - For any ML-based testing
- **Playwright** (1.61.0) - For browser automation
- **Chromium browser** - Installed and ready

## Running Tests

### Run all E2E tests
```bash
python run_e2e_tests.py
```

### Run with visible browser (headed mode)
```bash
python run_e2e_tests.py --headed
```

### Run specific test file
```bash
python run_e2e_tests.py --file tests/e2e/test_login.py
```

### Run specific test suite
```bash
python run_e2e_tests.py --suite auth
```

### Generate HTML report
```bash
python run_e2e_tests.py --report
```

### Run smoke tests only
```bash
python run_e2e_tests.py --suite smoke
```

## Test Structure

- `test_smoke.py` - Quick health checks
- `test_login.py` - Login and authentication flows
- `test_portfolio.py` - Portfolio management flows
- `test_pdf_import.py` - PDF import flows
- `test_reports.py` - Report generation flows

## Writing New Tests

1. Create a new file in `tests/e2e/` named `test_<feature>.py`
2. Use the `page` fixture for browser interactions
3. Follow the pattern of existing tests
4. Run tests with: `python run_e2e_tests.py --file tests/e2e/test_<feature>.py`

## Test Fixtures

- `browser_type` - Playwright browser type (session-scoped)
- `browser` - Browser instance (session-scoped)
- `page` - New page for each test (test-scoped)

## Troubleshooting

### Browser not found
```bash
python -m playwright install chromium
```

### Django server won't start
Check that `DJANGO_SECRET_KEY` and other required environment variables are set.

### Tests fail with connection errors
Ensure Django server is running on `127.0.0.1:8000`.
