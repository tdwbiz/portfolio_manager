"""Check date format in all PDFs."""
import re
from pathlib import Path

import fitz

pdfs = sorted(Path("inbox").glob("Statement-*.pdf"))[:10]

print("Checking date formats in first 10 PDFs...\n")

for pdf_path in pdfs:
    doc = fitz.open(pdf_path)
    all_text_parts = []
    for page in doc:
        all_text_parts.append(page.get_text())
    all_text = "\n".join(all_text_parts)
    doc.close()
    
    # Find first date in Account Activity
    for line in all_text.split('\n'):
        stripped = line.strip()
        # Check if this looks like a date
        m = re.match(r'^([A-Z]{3})\.?\s*(\d{1,2})', stripped)
        if m:
            month = m.group(1)
            day = m.group(2)
            dot = '.' if '.' in stripped else ' '
            print(f"{pdf_path.name[:30]}: '{month}{dot} {day}'")
            break
    else:
        print(f"{pdf_path.name[:30]}: No date found")
