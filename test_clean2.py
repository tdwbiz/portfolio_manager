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
acct_pos = result.lower().find("account")
print("acct_pos:", acct_pos)
if acct_pos != -1:
    after = result[acct_pos + 7:]
    print("after:", repr(after))
    digit_groups = re.findall(r"\d+", after)
    print("digit_groups:", digit_groups)
    if digit_groups:
        print("account_number:", "-".join(digit_groups))
for code in ["RRIF", "RRSP", "RIF", "LIF", "LRIF", "PRIF", "TFSA", "LIRA", "RESP", "IF"]:
    if re.search(re.escape(code) + r"Account", result, re.IGNORECASE):
        print("account_type:", code)
        break
