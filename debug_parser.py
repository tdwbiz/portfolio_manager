import tempfile
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force reload
for mod in list(sys.modules.keys()):
    if 'imports' in mod or 'parsers_annual' in mod:
        del sys.modules[mod]

from imports.parsers_annual import parse_rbc_annual_report, _clean_rbc_text

SAMPLE_2259 = """Annual Investment Performance Report
E L S I E S M A R A I S R R I F Account
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

Amount invested
Beginning value $80,000.00
Deposits and transfers-in $10,000.00
Withdrawals and transfers-out $5,000.00
Amount invested $85,000.00

Money-weighted rate of return
Past 1 year: 20.37%
Past 3 years: 15.93%
Past 5 years: 12.45%
Past 10 years: 10.22%
Since January 1, 2016: 11.87%

Your U.S. dollar account

Change in Account Value
Beginning market value $60,000.00
Deposits and transfers-in $8,000.00
Withdrawals and transfers-out $4,000.00
Net investment return $12,000.50
Ending market value $76,000.50
Amount invested $64,000.00

Money-weighted rate of return
Past 1 year: 19.50%
Past 3 years: 14.80%
Past 5 years: 11.90%
Past 10 years: 9.80%
Since January 1, 2016: 11.20%

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

Paid by you (USD)

Operating Charges $0.00
Commissions - Equities/options $35.00
Total transaction charges $35.00
Total paid by you $35.00

Third-party compensation (USD)

Trailing commission $4.50
Other compensation $1.50
Total compensation from third parties $6.00"""

tmpdir = tempfile.mkdtemp()
report_file = os.path.join(tmpdir, 'Annual Report-2259 2025-12-31.pdf')
with open(report_file, 'w') as f:
    f.write(SAMPLE_2259)

result = parse_rbc_annual_report(report_file)
print('account_number:', repr(result['account_number']))
print('account_type_code:', repr(result['account_type_code']))
print('balances:', len(result['balances']))
print('performance:', len(result['performance']))
print('charges:', len(result['charges']))
print('compensation:', len(result['compensation']))
if result['balances']:
    print('cad_bal:', result['balances'][0])
if result['performance']:
    print('cad_perf:', result['performance'][0])
if result['charges']:
    print('cad_charges:', result['charges'][:4])
if result['compensation']:
    print('cad_comp:', result['compensation'][:3])

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
