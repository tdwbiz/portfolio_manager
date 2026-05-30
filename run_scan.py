"""Run the PDF header scan and write results to file."""
import scan_pdf_headers

results = scan_pdf_headers.scan_pdfs()

# Count currencies
cad_count = sum(1 for r in results if r['currency'] == 'CAD')
usd_count = sum(1 for r in results if r['currency'] == 'USD')
unknown_count = sum(1 for r in results if r['currency'] == 'UNKNOWN')

# Write summary to file
with open('scan_summary.txt', 'w') as f:
    f.write(f"Total PDFs: {len(results)}\n")
    f.write(f"CAD: {cad_count}\n")
    f.write(f"USD: {usd_count}\n")
    f.write(f"Unknown: {unknown_count}\n\n")
    
    # Show sample Unknown files
    unknown_files = [r for r in results if r['currency'] == 'UNKNOWN']
    if unknown_files:
        f.write("First 10 Unknown files:\n")
        for uf in unknown_files[:10]:
            f.write(f"  {uf['filename']}: {uf['identifier'][:80]}\n")
    
    # Show sample USD files
    usd_files = [r for r in results if r['currency'] == 'USD']
    if usd_files:
        f.write(f"\nFirst 10 USD files:\n")
        for uf in usd_files[:10]:
            f.write(f"  {uf['filename']}: {uf['identifier'][:80]}\n")

print(f"Scan complete. Results written to scan_summary.txt")
print(f"Total: {len(results)}, CAD: {cad_count}, USD: {usd_count}, Unknown: {unknown_count}")
