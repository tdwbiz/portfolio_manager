from imports.parsers_pdf import _extract_text_from_pdf
import os
import re
from collections import defaultdict

# Check all PDFs in processed folder for statement header variations
processed_dir = 'processed'
inbox_dir = 'inbox'

all_dirs = [processed_dir, inbox_dir]

# Collect all unique header patterns
cad_patterns = set()
usd_patterns = set()

for d in all_dirs:
    if not os.path.exists(d):
        continue
    for fname in os.listdir(d):
        if not fname.lower().endswith('.pdf'):
            continue
        fpath = os.path.join(d, fname)
        try:
            text = _extract_text_from_pdf(fpath)
            lines = text.split('\n')
            for i, line in enumerate(lines):
                s = line.strip()
                # Look for statement-type headers with currency
                if ('Statement' in s or 'statement' in s or 'Dollar' in s or 'Cdn' in s or 'U.S.' in s or 'USD' in s or 'CAD' in s) and len(s) < 80:
                    # Determine if CAD or USD
                    is_usd = bool(re.search(r'(U\.S\.|U\.S|\$|USD|American)', s, re.IGNORECASE))
                    if is_usd:
                        usd_patterns.add(s)
                    else:
                        cad_patterns.add(s)
        except Exception as e:
            print(f"{fname}: ERROR - {e}")

print("=== CAD Statement Headers ===")
for p in sorted(cad_patterns):
    print(f"  [{p}]")

print("\n=== USD Statement Headers ===")
for p in sorted(usd_patterns):
    print(f"  [{p}]")
