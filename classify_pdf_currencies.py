"""
Classify all processed PDFs by currency format.
Uses text-based scanning (fast) to identify CAD vs USD statements.
"""
import os
import re

INBOX_DIR = 'processed'

CAD_PATTERNS = [
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA)\s+Statement\s+\(CDN\$\)', re.I),
    re.compile(r'Cdn\.\s*Dollar\s+Statement', re.I),
    re.compile(r'Your\s+Canadian\s+dollar\s+account', re.I),
]

USD_PATTERNS = [
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA)\s+Statement\s+\(U\.S\.\$\)', re.I),
    re.compile(r'U\.S\.\s*Dollar\s+Statement', re.I),
    re.compile(r'Your\s+U\.S\.\s*dollar\s+account', re.I),
    re.compile(r'Your\s+US\s+dollar\s+account', re.I),
]

def classify_pdf(filepath):
    """Classify a PDF as CAD, USD, or Unknown based on text content."""
    try:
        with open(filepath, 'r', errors='ignore') as f:
            text = f.read(50000)  # Read first 50KB
        
        has_cad = any(p.search(text) for p in CAD_PATTERNS)
        has_usd = any(p.search(text) for p in USD_PATTERNS)
        
        if has_usd and has_cad:
            return 'multi'
        elif has_usd:
            return 'USD'
        elif has_cad:
            return 'CAD'
        else:
            return 'Unknown'
    except Exception:
        return 'Error'

def main():
    pdf_files = sorted([f for f in os.listdir(INBOX_DIR) if f.endswith('.pdf')])
    print(f"Total PDFs: {len(pdf_files)}\n")
    
    counts = {'CAD': 0, 'USD': 0, 'multi': 0, 'Unknown': 0, 'Error': 0}
    files_by_type = {'CAD': [], 'USD': [], 'multi': [], 'Unknown': [], 'Error': []}
    
    for i, filename in enumerate(pdf_files):
        filepath = os.path.join(INBOX_DIR, filename)
        classification = classify_pdf(filepath)
        counts[classification] += 1
        files_by_type[classification].append(filename)
        
        if (i + 1) % 100 == 0:
            print(f"Processed {i + 1}/{len(pdf_files)}...")
    
    print(f"\n{'='*60}")
    print(f"CLASSIFICATION RESULTS")
    print(f"{'='*60}")
    print(f"CAD only:        {counts['CAD']:>5}")
    print(f"USD only:        {counts['USD']:>5}")
    print(f"Multi-currency:  {counts['multi']:>5}")
    print(f"Unknown:         {counts['Unknown']:>5}")
    print(f"Error:           {counts['Error']:>5}")
    print(f"Total:           {sum(counts.values()):>5}")
    
    print(f"\n{'='*60}")
    print(f"SAMPLE FILES BY TYPE")
    print(f"{'='*60}")
    
    for type_name in ['CAD', 'USD', 'multi', 'Unknown']:
        if files_by_type[type_name]:
            print(f"\n{type_name} ({len(files_by_type[type_name])} files):")
            for f in files_by_type[type_name][:5]:
                print(f"  - {f}")
            if len(files_by_type[type_name]) > 5:
                print(f"  ... and {len(files_by_type[type_name]) - 5} more")

if __name__ == '__main__':
    main()
