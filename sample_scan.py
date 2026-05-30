"""Quick sample scan of first 200 PDFs."""
import glob
import re
from imports.parsers_pdf import _extract_text_from_pdf

cad_p = [
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA)\s+Statement\s+\(CDN\$\)', re.IGNORECASE),
    re.compile(r'Cdn\.\s*Dollar\s+Statement', re.IGNORECASE),
    re.compile(r'Your\s+Canadian\s+dollar\s+account', re.IGNORECASE),
]
usd_p = [
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA)\s+Statement\s+\(U\.S\.\$\)', re.IGNORECASE),
    re.compile(r'U\.S\.\s*Dollar\s+Statement', re.IGNORECASE),
    re.compile(r'Your\s+U\.S\.\s*dollar\s+account', re.IGNORECASE),
    re.compile(r'Your\s+US\s+dollar\s+account', re.IGNORECASE),
]

files = sorted(glob.glob('processed/*.pdf'))
print(f'Total: {len(files)}')

cad = 0
usd = 0
unknown = 0

for i, f in enumerate(files[:200], 1):
    try:
        text = _extract_text_from_pdf(f)
        has_cad = any(p.search(text) for p in cad_p)
        has_usd = any(p.search(text) for p in usd_p)
        
        if has_usd:
            usd += 1
        elif has_cad:
            cad += 1
        else:
            unknown += 1
    except:
        unknown += 1

print(f'First 200: CAD={cad}, USD={usd}, Unknown={unknown}')
