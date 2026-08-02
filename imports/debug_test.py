"""Test parser output."""
from imports.parsers_monthly_vl import parse_rbc_monthly

# Find a sample PDF
import glob
pdfs = glob.glob("inbox/Statement-*.pdf")
if pdfs:
    pdf_path = pdfs[0]
    print(f"Parsing: {pdf_path}")
    result = parse_rbc_monthly(pdf_path)
    if result:
        txns = result['sections']['transactions']
        print(f"\nFound {len(txns)} transactions")
        for i, t in enumerate(txns[:3]):
            print(f"  [{i}] date={repr(t['date'])} (type={type(t['date']).__name__})")
            print(f"      act={t['activity_type']}, desc={t['description'][:50]}")
    else:
        print("Parser returned None")
else:
    print("No PDFs found")
