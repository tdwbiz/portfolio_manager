"""Debug script for account number parsing."""
from imports.parsers_annual import _clean_rbc_text, parse_rbc_annual_report
import os, tempfile

SAMPLE = """
Annual Investment Performance Report
E L S I E S M A R A I S R I F Account
Account 695- 62 2 59
from January 1, 2025 to December 31, 2025

Your Canadian dollar account

Change in Account Value
Beginning market value $80,000.00
Deposits and transfers-in $10,000.00
Withdrawals and transfers-out $5,000.00
Net investment return $15,000.34
Ending market value $100,000.34
Amount invested $85,000.00

Money-weighted rate of return
Past 1 year: 20.37%
Past 3 years: 15.93%
Past 5 years: 12.45%
Past 10 years: 10.22%
Since January 1, 2016: 11.87%

Annual Charges and Compensation Report

Paid by you (CAD)

Operating Charges $0.00
Commissions - Equities/options $49.75
Total transaction charges $49.75
Total paid by you $49.75

Third-party compensation (CAD)

Trailing commission $6.42
Other compensation $2.00
Total compensation from third parties $8.42
"""

tmpdir = tempfile.mkdtemp()
report_file = os.path.join(tmpdir, 'Annual Report-2259 2025-12-31.pdf')
with open(report_file, 'w') as f:
    f.write(SAMPLE)

# Test cleaning
print('=== Raw text (first 5 lines) ===')
for i, line in enumerate(SAMPLE.strip().split('\n')[:5]):
    print(f'  {i}: {repr(line)}')

print()
print('=== Cleaned text (first 5 lines) ===')
cleaned = _clean_rbc_text(SAMPLE)
for i, line in enumerate(cleaned.strip().split('\n')[:5]):
    print(f'  {i}: {repr(line)}')

print()

# Debug: manually test the account number regex
import re
pattern = re.compile(r'A[\s\n]*c[\s\n]*c[\s\n]*o[\s\n]*u[\s\n]*n[\s\n]*t', re.IGNORECASE)
print('=== Regex matches in cleaned text ===')
for m in pattern.finditer(cleaned):
    pos = m.start()
    window = cleaned[pos:pos+50]
    digit_groups = re.findall(r'\d+', window)
    print(f'  Match at pos {pos}: {repr(m.group())}')
    print(f'    window: {repr(window)}')
    print(f'    digit_groups: {digit_groups}')
    if digit_groups:
        prefix = digit_groups[0]
        suffix = ''.join(digit_groups[1:])
        print(f'    prefix={prefix} (len={len(prefix)}), suffix={suffix} (len={len(suffix)})')
        if prefix and len(prefix) == 3 and suffix and 3 <= len(suffix) <= 5:
            print(f'    => WOULD MATCH: {prefix}-{suffix}')
        else:
            print(f'    => DOES NOT MATCH (prefix len={len(prefix)} or suffix len={len(suffix)})')
    print()

result = parse_rbc_annual_report(report_file)
print(f'account_number: {result["account_number"]}')
print(f'account_type_code: {result["account_type_code"]}')
print(f'report_year: {result["report_year"]}')

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
