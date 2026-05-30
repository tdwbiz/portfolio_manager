"""Quick diagnostic: show RAW text around balance section."""
import re, sys
sys.path.insert(0, '.')
import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')
django.setup()

from imports.parsers_annual import _extract_text_from_pdf

pdf = 'specs/Annual Report-2259 2025-12-31.pdf'
raw = _extract_text_from_pdf(pdf)

lines = raw.split('\n')
print(f"Total raw lines: {len(lines)}")

# Show lines 910-970 (around the "Change" at 927)
print("\n=== RAW lines 910-960 ===")
for i in range(910, 960):
    print(f"  [{i}]: {repr(lines[i])}")

# Show lines 400-440 (around "weighted" at 418)
print("\n=== RAW lines 400-440 ===")
for i in range(400, 440):
    print(f"  [{i}]: {repr(lines[i])}")


lines = cleaned.split('\n')
print(f"Total lines: {len(lines)}")

# Find and show balances section
for i, line in enumerate(lines):
    if BALANCES_HEADER.search(line):
        print(f"\n=== BALANCES HEADER at line {i} ===")
        for j in range(i, min(i+25, len(lines))):
            print(f"  [{j}]: {repr(lines[j])}")
        break
else:
    print("BALANCES_HEADER not found in cleaned text!")
    print("\nSearching for 'Change' in any form:")
    for i, line in enumerate(lines):
        if 'change' in line.lower() or 'account value' in line.lower():
            print(f"  [{i}]: {repr(line)}")

# Find performance section
for i, line in enumerate(lines):
    if PERFORMANCE_TABLE_HEADER.search(line):
        print(f"\n=== PERFORMANCE HEADER at line {i} ===")
        for j in range(i, min(i+20, len(lines))):
            print(f"  [{j}]: {repr(lines[j])}")
        break
else:
    print("\nPERFORMANCE_TABLE_HEADER not found!")
    print("Searching for 'money' or 'weighted':")
    for i, line in enumerate(lines):
        if 'money' in line.lower() or 'weighted' in line.lower() or 'rate of return' in line.lower():
            print(f"  [{i}]: {repr(line)}")
