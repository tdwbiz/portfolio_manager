"""Test the updated parser with multi-currency PDFs."""
import sys
sys.path.insert(0, '.')
from imports.parsers_pdf import parse_rbc_pdf_multi

# Test files
test_files = [
    'processed/90000003-2014Apr30-2014Apr30_20260524_212338.pdf',  # Multi-currency (CDN$ + U.S.$)
    'processed/90000003-2017Dec31-2017Dec31_20260524_212342.pdf',  # Multi-currency (Canadian dollar + U.S. dollar)
    'processed/90000005-2017Dec31-2017Dec31_20260524_212332.pdf',  # Multi-currency (Canadian dollar + U.S. dollar)
    'processed/90000007-2022Nov30-2022Nov30_20260524_212336.pdf',  # Cdn. Dollar Statement (CAD only)
]

for pdf_file in test_files:
    print(f"\n{'='*80}")
    print(f"Testing: {pdf_file}")
    print('='*80)
    try:
        results = parse_rbc_pdf_multi(pdf_file)
        print(f'Found {len(results)} currency section(s):')
        for i, r in enumerate(results):
            print(f'  Section {i+1}:')
            print(f'    currency: {r["currency"]}')
            print(f'    statement_date: {r["statement_date"]}')
            print(f'    account_number: {r["account_number"]}')
            print(f'    fx_rate: {r["fx_rate"]}')
            print(f'    balances: {len(r["balances"])}')
            print(f'    transactions: {len(r["transactions"])}')
            print(f'    income_summary: {r["income_summary"]}')
    except Exception as e:
        print(f'ERROR: {e}')
        import traceback
        traceback.print_exc()
