import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()

from imports.parsers_annual import parse_rbc_annual_report

files = [
    'inbox/Annual Report-2259 2017-12-31_20260529_191611.pdf',
    'inbox/Annual Report-2259 2022-12-31_20260529_191613.pdf',
    'inbox/Annual Report-4033 2019-12-31_20260529_191615.pdf',
    'inbox/Annual Report-5362 2017-12-31_20260529_191630.pdf',
    'inbox/Annual Report-7620 2018-12-31_20260529_191634.pdf',
]

for f in files:
    try:
        result = parse_rbc_annual_report(f)
        print(f"File: {os.path.basename(f)}")
        print(f"  Account: {result.get('account_number', 'N/A')}")
        print(f"  Type: {result.get('account_type_code', 'N/A')}")
        print(f"  Year: {result.get('report_year', 'N/A')}")
        print(f"  Balances: {len(result.get('balances', []))}")
        print(f"  Performance: {len(result.get('performance', []))}")
        print(f"  Charges: {len(result.get('charges', []))}")
        print(f"  Compensation: {len(result.get('compensation', []))}")
        print()
    except Exception as e:
        print(f"File: {os.path.basename(f)}")
        print(f"  ERROR: {e}")
        print()
