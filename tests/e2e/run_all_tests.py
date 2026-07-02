#!/usr/bin/env python
"""
Run all end-to-end tests and generate a comprehensive report.
"""
import subprocess
import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

e2e_dir = Path(__file__).parent

def run_tests():
    """Run all e2e tests and generate report."""
    print("=" * 80)
    print("PORTFOLIO MANAGER - END-TO-END TEST SUITE")
    print("=" * 80)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version}")
    print()
    
    # Change to e2e directory
    os.chdir(e2e_dir)
    
    # Run pytest with comprehensive options
    print("Running test suite...")
    print("-" * 80)
    
    cmd = [
        sys.executable, "-m", "pytest",
        "test_complete_app.py",
        "-v",
        "--tb=short",
        "--html=report.html",
        "--self-contained-html",
        "-x",  # Stop on first failure
    ]
    
    start_time = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed_time = time.time() - start_time
    
    # Print test output
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    
    # Generate summary report
    generate_report(result, elapsed_time)
    
    return result.returncode

def generate_report(result, elapsed_time):
    """Generate a comprehensive test report."""
    report = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "elapsed_time": f"{elapsed_time:.2f}s",
        "return_code": result.returncode,
        "status": "PASSED" if result.returncode == 0 else "FAILED",
        "output": result.stdout,
    }
    
    # Count tests
    lines = result.stdout.split('\n')
    test_count = sum(1 for line in lines if 'PASSED' in line or 'FAILED' in line or 'ERROR' in line)
    passed = sum(1 for line in lines if 'PASSED' in line)
    failed = sum(1 for line in lines if 'FAILED' in line)
    errors = sum(1 for line in lines if 'ERROR' in line)
    
    report["tests"] = {
        "total": test_count,
        "passed": passed,
        "failed": failed,
        "errors": errors,
    }
    
    # Write report
    report_file = e2e_dir / "test_report.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Status: {report['status']}")
    print(f"Total Tests: {test_count}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Errors: {errors}")
    print(f"Elapsed Time: {elapsed_time:.2f}s")
    print(f"\nDetailed report: {report_file}")
    print(f"HTML report: {e2e_dir / 'report.html'}")

if __name__ == "__main__":
    exit(run_tests())
