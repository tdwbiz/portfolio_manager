"""Quick scan to get CAD/USD counts."""
import scan_pdf_headers

results = scan_pdf_headers.scan_pdfs()
cad = sum(1 for r in results if r['currency'] == 'CAD')
usd = sum(1 for r in results if r['currency'] == 'USD')
unknown = sum(1 for r in results if r['currency'] == 'UNKNOWN')

# Write to file
with open('quick_scan_results.txt', 'w') as f:
    f.write(f'Total: {len(results)}, CAD: {cad}, USD: {usd}, Unknown: {unknown}\n')
    
    # Show some Unknown files
    unknown_files = [r for r in results if r['currency'] == 'UNKNOWN']
    if unknown_files:
        f.write(f'\nFirst 5 Unknown files:\n')
        for file in unknown_files[:5]:
            f.write(f"  {file['filename']}: {file['identifier'][:80]}\n")

print("Results written to quick_scan_results.txt")
