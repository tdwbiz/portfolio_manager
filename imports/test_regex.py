import re

test_string = "VANGUARD S&P 500 INDEX ETF"

# Test with different patterns
patterns = [
    r"^[A-Za-z\s'\-\.\&\,]+$",
    r"^[A-Za-z\s'\-\.&]+ [A-Z]{1,3}",  # Allow space before S&P
    r"^[A-Za-z\s'\-\.&\d]+",  # Allow digits
    r"^[A-Za-z\s'\-\.&]+",  # Just letters and symbols
]

for p in patterns:
    m = re.match(p, test_string)
    print(f"Pattern: {p}")
    print(f"  Match: {bool(m)}")

# Now let's see what the actual security name line looks like
print(f"\nTest string: '{test_string}'")
print(f"  Contains &: { '&' in test_string}")
print(f"  Contains 500: {'500' in test_string}")

# The issue: "VANGUARD S&P 500 INDEX ETF" has digits in it!
# So the regex should fail because we check "not re.search(r'\d', stripped)"
# But we ALSO have the \d in the character class... wait no we don't
# The pattern is [A-Za-z\s'\-\.&\,]+ which does NOT include \d
# So "500" won't match

# But wait - the test says is_name=False for this line
# Let me check if the & character is the issue
print("\n  Character by character:")
for c in test_string:
    if not re.match(r"[A-Za-z\s'\-\.&\,]", c):
        print(f"    FAIL: '{c}' (ord={ord(c)})")
