# Feature Spec: Statement Date Extraction & LLM Fallback

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature_rbc_pdf_fixes

---

## Context

Two critical robustness issues in the import pipeline:

1. **Statement date uses import time** (`imports/auto_import.py`): `file_date=timezone.now().date()` records when the file was imported, not the actual statement date. This breaks historical reporting and time-series analysis.

2. **LLM failure halts entire import** (`imports/parsers_monthly_vl.py`): If the VL model fails to extract `statement_date`, the entire import fails with a hard error. No retry, no fallback, no partial success.

**Related issues:** U1 (statement date), U2 (LLM failure).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. **Extract actual statement date from files**

#### 1a. For CSV imports (`auto_import.py`)

- Extract the date from the first transaction in the parsed trades
- If no transactions have dates, fall back to the file's modification date
- Create a helper function `extract_statement_date(trades, file_path, broker)` in `imports/auto_import.py`

```python
from datetime import date
from pathlib import Path

def extract_statement_date(trades, file_path, broker):
    """Extract the statement date from parsed trades or file metadata.
    
    Priority:
    1. First transaction date from trades
    2. File modification date
    3. Today as last resort (with warning)
    """
    if trades:
        first_date = trades[0].get('date')
        if first_date:
            return first_date
    
    # Fallback to file modification date
    try:
        mtime = Path(file_path).stat().st_mtime
        return date.fromtimestamp(mtime)
    except OSError:
        pass
    
    # Last resort
    from django.utils import timezone
    logger.warning(f"Could not extract statement date for {file_path}, using today")
    return timezone.now().date()
```

#### 1b. For VL PDF imports (`parsers_monthly_vl.py`)

- The VL model already returns `statement_date` in its JSON output
- If the VL model returns a date, use it (no change needed)
- If the VL model returns `None`, fall back to extracting from filename

```python
def extract_date_from_filename(filename):
    """Try to extract a date from common RBC filename patterns.
    
    Examples:
    - 'RBC_20250131_Statement.pdf' -> 2025-01-31
    - 'statement_2025-01-31.pdf' -> 2025-01-31
    """
    import re
    patterns = [
        r'(\d{4})[-_](\d{2})[-_](\d{2})',  # YYYY-MM-DD or YYYY_MM_DD
        r'(\d{2})[-_](\d{2})[-_](\d{4})',  # MM-DD-YYYY or DD_MM_YYYY
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                try:
                    if len(groups[0]) == 4:
                        return date(int(groups[0]), int(groups[1]), int(groups[2]))
                    else:
                        return date(int(groups[2]), int(groups[0]), int(groups[1]))
                except ValueError:
                    continue
    return None
```

### 2. **Add LLM failure fallback mechanism**

#### 2a. Retry logic for VL parsing

- If the VL model returns an error or empty response, retry up to 2 times with a 5-second delay
- After retries, attempt regex-based fallback extraction from the raw PDF text

```python
def parse_rbc_monthly_vl(file_path, max_retries=2):
    """Parse RBC monthly PDF using VL model with retry and fallback.
    
    Returns a list of section dicts, or raises ValueError only if all
    extraction methods fail.
    """
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            result = _parse_with_vl(file_path)
            if result and _validate_result(result):
                return result
            logger.warning(f"VL attempt {attempt}/{max_retries}: empty or invalid result")
        except VLParseError as e:
            last_error = e
            logger.warning(f"VL attempt {attempt}/{max_retries} failed: {e}")
        
        if attempt < max_retries:
            time.sleep(5)
    
    # All VL retries failed — attempt regex fallback
    logger.warning(f"VL model failed after {max_retries} retries, attempting regex fallback for {file_path}")
    return _parse_with_regex_fallback(file_path)
```

#### 2b. Regex fallback for PDF text extraction

- Use existing `_split_pdf_into_sections()` from `imports/parsers_pdf.py`
- Extract dates, balances, and transactions using regex patterns
- Return a minimal valid result that can be saved to the database

```python
def _parse_with_regex_fallback(file_path):
    """Fallback parser using regex on extracted PDF text.
    
    Returns a minimal valid result dict. May have missing fields.
    """
    from imports.parsers_pdf import _split_pdf_into_sections, _extract_date_from_filename
    
    sections = _split_pdf_into_sections(file_path)
    result = []
    
    for section in sections:
        statement_date = _extract_date_from_filename(os.path.basename(file_path))
        if not statement_date:
            # Try to extract from section text
            text = section.get('text', '')
            date_match = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})', text)
            if date_match:
                statement_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
        
        result.append({
            'statement_date': statement_date,
            'account_number': section.get('account_number', ''),
            'currency': section.get('currency', 'CAD'),
            'balances': section.get('balances', []),
            'transactions': section.get('transactions', []),
        })
    
    return result
```

---

## Constraints

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- The regex fallback should be a best-effort — missing fields are acceptable
- All existing tests must continue to pass

---

## Files to Modify

1. `imports/auto_import.py` — Add `extract_statement_date()` helper, use it for `file_date`
2. `imports/parsers_monthly_vl.py` — Add retry logic and regex fallback
3. `imports/parsers_pdf.py` — (Read-only reference for existing regex patterns)

---

## Tests

- Unit test for `extract_statement_date()`: Test with trades list, with file mtime, with no data
- Unit test for VL retry: Mock VL to fail twice, succeed on third attempt
- Unit test for regex fallback: Test with a sample PDF that VL cannot parse
- Unit test for filename date extraction: Test with various filename patterns

---

## Manual Steps Required

- [ ] `python manage.py makemigrations` (if any model changes)
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature_rbc_pdf_fixes`
- [ ] Run full test suite: `python run_tests.py`

---

## Human Approval Required

Human approval required before any git push or DB changes.
