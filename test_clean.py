import re

SAMPLE = "Annual Investment Performance Report\nE L S I E S M A R A I S R R I F Account\nAccount 695- 62 2 59\nfrom January 1, 2025 to December 31, 2025"
lines = SAMPLE.split("\n")
cleaned_lines = []
for line in lines:
    cleaned = line
    for _ in range(20):
        new = re.sub(r"([a-zA-Z])\s+([a-zA-Z])", r"\1\2", cleaned)
        if new == cleaned:
            break
        cleaned = new
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned_lines.append(cleaned)
result = "\n".join(cleaned_lines)
print(result)
print("---")
m = re.search(r"([A-Z]{2,4})\s+Account", result)
print("type:", m.group(1) if m else None)
m2 = re.search(r"Account\s+(\d+)\s*[-\s]*(\d+)", result)
print("num:", m2.group(1) + "-" + m2.group(2) if m2 else None)
