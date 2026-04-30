# Feature Spec: Import History UI with Rejection Log Downloads

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/import-history-ui

---

## Context

Specs 01–07 are complete. The `FileImportLog` model (in `core/models.py`) records every
import run: filename, broker, imported_at, records_imported, records_skipped, status,
error_message. Currently this data is only visible in Django admin.

Rejection logs are written to `IMPORT_LOGS_DIR` (defaults to `BASE_DIR/logs/`) as
`rejected_YYYY_MM.csv` files (Spec 03). The `logs/` folder is gitignored.

This spec adds an import history page in `core` and a file-serve view for rejection
log downloads.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

1. **New view `import_history` in `core/views.py`**
   - URL: `/imports/`
   - Queries `FileImportLog.objects.all().order_by('-imported_at')`.
   - Paginates at 25 per page.
   - For each log entry, determines whether a matching rejection log file exists:
     - Parse the `imported_at` date to get year+month.
     - Check if `settings.IMPORT_LOGS_DIR / f'rejected_{YYYY}_{MM}.csv'` exists using `pathlib.Path.exists()`.
     - Pass a boolean `has_rejection_log` and the `YYYY_MM` string to use in the download link.
   - Build list of dicts with extra fields added to each log: `{'log': log_obj, 'rejection_month': 'YYYY_MM', 'has_rejection_log': bool}`.
   - Passes to template: `page_obj`, and the enriched list paired with pagination.
   - Alternatively: pass the enriched list as `entries` alongside `page_obj` (implementer may choose approach).

2. **New view `download_rejection_log` in `core/views.py`**
   - URL: `/imports/rejection-log/<str:month>/` where `month` is in format `YYYY_MM`.
   - Validates `month` matches pattern `^\d{4}_\d{2}$` using `re.match`. Return 400 if invalid.
   - Constructs path: `settings.IMPORT_LOGS_DIR / f'rejected_{month}.csv'`.
   - If file does not exist, return 404.
   - Serves the file as a CSV download:
     - `Content-Type: text/csv`
     - `Content-Disposition: attachment; filename="rejected_{month}.csv"`
   - Read and stream using `FileResponse` with `open(path, 'rb')`.
   - Import `FileResponse` from `django.http` and `re` from stdlib.

3. **New URLs in `core/urls.py`**
   - Add after existing paths:
     ```python
     path('imports/', views.import_history, name='import_history'),
     path('imports/rejection-log/<str:month>/', views.download_rejection_log, name='download_rejection_log'),
     ```

4. **Add nav link in `core/templates/core/base.html`**
   - Add after the "Accounts" link:
     ```html
     <a href="{% url 'import_history' %}">Import History</a>
     ```

5. **New template `core/templates/core/import_history.html`**
   - Extends `core/base.html`.
   - Header: `<h1>Import History</h1>`
   - Table columns: Imported At | Filename | Broker | Records Imported | Records Skipped | Status | Error | Rejection Log
   - "Rejection Log" column: if `entry.has_rejection_log` is true, show a download link:
     ```html
     <a href="{% url 'download_rejection_log' entry.rejection_month %}">Download CSV</a>
     ```
     Otherwise show `—`.
   - Truncate `error_message` to 80 characters with `…` if longer.
   - Standard pagination links (previous/next page).

6. **Tests in `core/tests/test_import_history.py`**
   - `TestCase` class `ImportHistoryTests`.
   - `setUp`: create 3 `FileImportLog` objects with different `imported_at` times and statuses.
   - `test_import_history_loads`: GET `/imports/` returns 200.
   - `test_import_history_shows_filename`: response contains filename of a created log.
   - `test_rejection_log_download_invalid_month`: GET `/imports/rejection-log/badmonth/` returns 400.
   - `test_rejection_log_download_not_found`: GET `/imports/rejection-log/2099_01/` returns 404 (file won't exist).
   - `test_rejection_log_download_ok`: create a real temp file at `settings.IMPORT_LOGS_DIR / 'rejected_2024_01.csv'` using `tempfile` / `tmp_path`, mock `settings.IMPORT_LOGS_DIR` to point to the temp dir, GET `/imports/rejection-log/2024_01/` returns 200 with content-type `text/csv`.

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations.
- The `download_rejection_log` view must validate the `month` parameter with regex before constructing any file path (prevents path traversal).
- Do NOT serve files outside of `settings.IMPORT_LOGS_DIR`.
- Run `python manage.py test core --settings=portfolio_manager.settings_test`. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: import history UI with rejection log downloads"`

---

## Manual Steps Required

- [ ] Review and push feature branch: `git push origin feature/import-history-ui`
- [ ] Merge to dev after review.
