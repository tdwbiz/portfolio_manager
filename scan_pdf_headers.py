"""Scan all PDFs in processed folder to find currency identifiers."""
import os
import sys
import re
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imports.parsers_pdf import _extract_text_from_pdf

PROCESSED_DIR = Path('processed')

# Comprehensive patterns to identify currency in statement headers
# These match the patterns in imports/parsers_pdf.py
CAD_PATTERNS = [
    # Standard statement headers
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA)\s+Statement\s+\(CDN\$\)', re.IGNORECASE),
    # Cdn. Dollar format
    re.compile(r'Cdn\.\s*Dollar\s+Statement', re.IGNORECASE),
    # Your dollar account format
    re.compile(r'Your\s+Canadian\s+dollar\s+account', re.IGNORECASE),
]

USD_PATTERNS = [
    # Standard statement headers
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA)\s+Statement\s+\(U\.S\.\$\)', re.IGNORECASE),
    # U.S. Dollar format
    re.compile(r'U\.S\.\s*Dollar\s+Statement', re.IGNORECASE),
    # Your dollar account format
    re.compile(r'Your\s+U\.S\.\s*dollar\s+account', re.IGNORECASE),
    re.compile(r'Your\s+US\s+dollar\s+account', re.IGNORECASE),
]

def identify_currency(text):
    """Identify if text is CAD or USD based on header patterns.
    
    Returns ('CAD', line) or ('USD', line) or ('UNKNOWN', first_line).
    Scans entire text to detect multi-currency PDFs.
    Priority: USD detection first (to identify multi-currency PDFs).
    """
    lines = text.split('\n')
    
    # Scan entire text for both CAD and USD headers
    cad_lines = []
    usd_lines = []
    
    for line in lines:
        for pattern in CAD_PATTERNS:
            if pattern.search(line):
                cad_lines.append(line.strip())
                break
        for pattern in USD_PATTERNS:
            if pattern.search(line):
                usd_lines.append(line.strip())
                break
    
    # If we found USD headers, this is a multi-currency PDF
    if usd_lines:
        return 'USD', usd_lines[0]
    
    # If we only found CAD headers, this is CAD-only
    if cad_lines:
        return 'CAD', cad_lines[0]
    
    return 'UNKNOWN', lines[0].strip() if lines else 'No header found'

def scan_pdfs():
    """Scan all PDFs in processed folder."""
    results = []
    
    if not PROCESSED_DIR.exists():
        print(f"Processed directory not found: {PROCESSED_DIR}")
        return results
    
    pdf_files = sorted(PROCESSED_DIR.glob('*.pdf'))
    print(f"Found {len(pdf_files)} PDF files in {PROCESSED_DIR}")
    
    for i, pdf_file in enumerate(pdf_files, 1):
        try:
            print(f"[{i}/{len(pdf_files)}] Processing {pdf_file.name}...")
            text = _extract_text_from_pdf(str(pdf_file))
            currency, identifier = identify_currency(text)
            results.append({
                'filename': pdf_file.name,
                'currency': currency,
                'identifier': identifier
            })
            print(f"  -> {currency}: {identifier[:100]}")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results.append({
                'filename': pdf_file.name,
                'currency': 'ERROR',
                'identifier': str(e)
            })
    
    return results

if __name__ == '__main__':
    results = scan_pdfs()
    
    print("PDF Currency Identification Results")
    print("=" * 80)
    
    for result in results:
        print(f"File: {result['filename']}")
        print(f"  Currency: {result['currency']}")
        print(f"  Identifier: {result['identifier']}")
        print()
    
    # Summary
    cad_count = sum(1 for r in results if r['currency'] == 'CAD')
    usd_count = sum(1 for r in results if r['currency'] == 'USD')
    unknown_count = sum(1 for r in results if r['currency'] == 'UNKNOWN')
    error_count = sum(1 for r in results if r['currency'] == 'ERROR')
    
    print("Summary:")
    print(f"  CAD: {cad_count}")
    print(f"  USD: {usd_count}")
    print(f"  Unknown: {unknown_count}")
    print(f"  Errors: {error_count}")
