"""Fast scan - check first 5 lines of each PDF for currency."""
import glob
import re
from imports.parsers_pdf import _extract_text_from_pdf

CAD_PATTERNS = [
    re.compile(r'RRSP\s+Statement\s+\(CDN\$\)', re.IGNORECASE),
    re.compile(r'TFSA\s+Statement\s+\(CDN\$\)', re.IGNORECASE),
    re.compile(r'LIF\s+Statement\s+\(CDN\$\)', re.IGNORECASE),
    re.compile(r'FHSA\s+Statement\s+\(CDN\$\)', re.IGNORECASE),
    re.compile(r'Cdn\.\s*Dollar\s+Statement', re.IGNORECASE),
    re.compile(r'Your\s+Canadian\s+dollar\s+account', re.IGNORECASE),
]

USD_PATTERNS = [
    re.compile(r'RRSP\s+Statement\s+\(U\.S\.\$\)', re.IGNORECASE),
    re.compile(r'TFSA\s+Statement\s+\(U\.S\.\$\)', re.IGNORECASE),
    re.compile(r'LIF\s+Statement\s+\(U\.S\.\$\)', re.IGNORECASE),
    re.compile(r'FHSA\s+Statement\s+\(U\.S\.\$\)', re.IGNORECASE),
    re.compile(r'U\.S\.\s*Dollar\s+Statement', re.IGNORECASE),
    re.compile(r'Your\s+U\.?\s*S?\s*dollar\s+account', re.IGNORECASE),
]

files = sorted(glob.glob('processed/*.pdf'))
print(f'Total files: {len(files)}')

cad_count = 0
usd_count = 0
unknown_files = []

for i, f in enumerate(files, 1):
    try:
        text = _extract_text_from_pdf(f)
        lines = text.split('\n')[:50]  # Check first 50 lines
        
        is_cad = any(p.search(line) for line in lines for p in CAD_PATTERNS)
        is_usd = any(p.search(line) for line in lines for p in USD_PATTERNS)
        
        if is_usd:
            usd_count += 1
        elif is_cad:
            cad_count += 1
        else:
            unknown_files.append(f)
            
    except Exception as e:
        unknown_files.append(f"{f} (error: {e})")

print(f'CAD: {cad_count}, USD: {usd_count}, Unknown: {len(unknown_files)}')

if unknown_files:
    print(f'\nFirst 10 Unknown files:')
    for uf in unknown_files[:10]:
        if isinstance(uf, str) and uf.startswith('processed/'):
            try:
                text = _extract_text_from_pdf(uf)
                first_line = text.split('\n')[0][:80]
                print(f"  {uf}: {first_line}")
            except:
                print(f"  {uf}: (error reading)")
        else:
            print(f"  {uf}")
