#!/usr/bin/env python
"""
Automated E2E Test Runner for portfolio_manager

This script runs all Playwright-based end-to-end tests to simulate
real user interactions with the application.

Usage:
    python run_e2e_tests.py              # Run all E2E tests
    python run_e2e_tests.py --headed     # Run with visible browser
    python run_e2e_tests.py --file tests/test_e2e_user_journey.py  # Run specific test file
    python run_e2e_tests.py --suite auth  # Run specific test suite
    python run_e2e_tests.py --report      # Generate HTML report
"""
import sys
import os
import argparse
import subprocess
import time
import signal
from pathlib import Path

# Setup Django
project_root = str(Path(__file__).parent)
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')

import django
django.setup()


def print_header(text):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70 + "\n")


def run_tests(test_path=None, headed=False, report=False, suite=None):
    """Run Playwright E2E tests using pytest."""
    
    # Base pytest command
    cmd = [
        sys.executable, '-m', 'pytest',
        'tests/',
        '-v',
        '--tb=short',
        '--strict-markers',
        '-x',  # Stop on first failure
    ]
    
    # Add test path if specified
    if test_path:
        cmd.append(test_path)
    
    # Add headed mode if requested
    if headed:
        cmd.append('--headed')
        print_header("Running E2E Tests in HEADED Mode (Browser Visible)")
    else:
        print_header("Running E2E Tests in HEADLESS Mode")
    
    # Add suite selection if specified
    if suite:
        suite_map = {
            'auth': '-k "TestUserAuthentication"',
            'navigation': '-k "TestDashboardNavigation"',
            'portfolio': '-k "TestPortfolioFeatures"',
            'import': '-k "TestPDFImportFlow"',
            'responsive': '-k "TestResponsiveDesign"',
            'forms': '-k "TestFormValidation"',
            'workflows': '-k "TestCompleteUserOnboarding or TestTransactionManagement or TestReportGeneration"',
            'security': '-k "TestSecurityFeatures"',
            'performance': '-k "TestPerformanceMetrics"',
            'api': '-k "TestAPIEndpoints or TestChartRendering or TestRealTimeFeatures"',
        }
        if suite in suite_map:
            cmd.extend(suite_map[suite].split())
        else:
            print(f"Warning: Unknown suite '{suite}'. Running all tests.")
    
    # Add HTML report generation if requested
    if report:
        try:
            import pytest_html
            cmd.extend(['--html=tests/test_report.html', '--self-contained-html'])
            print("HTML report will be generated at: tests/test_report.html")
        except ImportError:
            print("Warning: pytest-html not installed. Install with: pip install pytest-html")
    
    # Print test command
    print(f"Running: {' '.join(cmd)}\n")
    
    # Run tests
    try:
        result = subprocess.run(cmd, cwd=project_root, check=False)
        return result.returncode
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user.")
        return 130
    except Exception as e:
        print(f"\nError running tests: {e}")
        return 1


def show_test_summary():
    """Show available test suites and files."""
    print_header("Available E2E Test Suites")
    
    suites = {
        'auth': 'User Authentication Tests',
        'navigation': 'Dashboard & Navigation Tests',
        'portfolio': 'Portfolio Management Tests',
        'import': 'PDF Import Tests',
        'responsive': 'Responsive Design Tests',
        'forms': 'Form Validation Tests',
        'workflows': 'User Workflow Tests',
        'security': 'Security Feature Tests',
        'performance': 'Performance Metrics Tests',
        'api': 'API & Chart Tests',
    }
    
    print("Test Suites:")
    for key, desc in suites.items():
        print(f"  {key:<15} - {desc}")
    
    print("\nTest Files:")
    test_files = [
        'tests/test_e2e_user_journey.py',
        'tests/test_user_workflows.py',
        'tests/test_api_endpoints.py',
    ]
    
    for tf in test_files:
        path = Path(project_root) / tf
        if path.exists():
            print(f"  ✓ {tf}")
        else:
            print(f"  ✗ {tf} (not found)")
    
    print("\n" + "=" * 70)
    print("  Quick Start Commands:")
    print("=" * 70)
    print("  Run all tests:          python run_e2e_tests.py")
    print("  Run with browser:       python run_e2e_tests.py --headed")
    print("  Run auth tests:         python run_e2e_tests.py --suite auth")
    print("  Run specific file:      python run_e2e_tests.py --file tests/test_e2e_user_journey.py")
    print("  Generate HTML report:   python run_e2e_tests.py --report")
    print("=" * 70 + "\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Run E2E tests for portfolio_manager using Playwright',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_e2e_tests.py                    Run all E2E tests
  python run_e2e_tests.py --headed           Run with visible browser
  python run_e2e_tests.py --suite auth       Run authentication tests only
  python run_e2e_tests.py --file tests/test_e2e_user_journey.py
  python run_e2e_tests.py --report           Generate HTML report
        """
    )
    
    parser.add_argument(
        '--headed',
        action='store_true',
        help='Run tests with visible browser (not headless)'
    )
    parser.add_argument(
        '--file',
        type=str,
        help='Run specific test file'
    )
    parser.add_argument(
        '--suite',
        type=str,
        help='Run specific test suite (auth, navigation, portfolio, import, responsive, forms, workflows, security, performance, api)'
    )
    parser.add_argument(
        '--report',
        action='store_true',
        help='Generate HTML test report'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available test suites and exit'
    )
    
    args = parser.parse_args()
    
    if args.list:
        show_test_summary()
        return 0
    
    print_header("Portfolio Manager - E2E Test Runner")
    print("Testing Version: Simulated User Interactions")
    print("Framework: Playwright + pytest")
    print("Django Settings: portfolio_manager.settings_test")
    
    # Run tests
    exit_code = run_tests(
        test_path=args.file,
        headed=args.headed,
        report=args.report,
        suite=args.suite
    )
    
    if exit_code == 0:
        print_header("✓ ALL TESTS PASSED")
    else:
        print_header(f"✗ TESTS FAILED (Exit Code: {exit_code})")
    
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
