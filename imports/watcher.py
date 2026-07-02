"""Shared inbox-watcher logic.

Used by both:
  - imports/apps.py  (AppConfig.ready — background daemon thread with runserver)
  - imports/management/commands/watch_inbox.py  (standalone management command)
"""

import logging
import os
import re
import threading
import time

import fitz  # PyMuPDF

from django.conf import settings
from django.core.management import call_command

logger = logging.getLogger(__name__)


def _classify_pdf(file_path: str) -> str:
    """Classify a PDF file by examining its content (not just filename).

    Reads the first page's text and looks for keywords to determine if it's
    an annual report or a monthly statement.

    Args:
        file_path: Path to the PDF file.

    Returns:
        'annual' or 'statement'.
    """
    try:
        doc = fitz.open(file_path)
        first_page = doc[0]
        text = first_page.get_text().lower()
        doc.close()

        annual_keywords = ['annual report', 'annual summary', 'year-end', 'annual performance']
        statement_keywords = ['monthly statement', 'statement', 'as of', 'period end']

        for kw in annual_keywords:
            if kw in text:
                return 'annual'

        for kw in statement_keywords:
            if kw in text:
                return 'statement'

        # Fallback: check filename
        basename = os.path.basename(file_path).lower()
        if 'annual' in basename:
            return 'annual'

    except Exception as e:
        logger.warning(f"_classify_pdf: failed to read {file_path}: {e}")

    # Default fallback to filename-based classification
    basename = os.path.basename(file_path).lower()
    if 'annual' in basename:
        return 'annual'
    return 'statement'


def _poll_once(inbox_dir: str) -> None:
    """Check inbox for CSVs and/or PDFs and run imports if any are found."""
    if not os.path.isdir(inbox_dir):
        return
    csv_files = [f for f in os.listdir(inbox_dir) if f.lower().endswith('.csv')]
    all_pdf_files = [f for f in os.listdir(inbox_dir) if f.lower().endswith('.pdf')]
    if csv_files:
        logger.info(f"watcher: found {len(csv_files)} CSV file(s) in {inbox_dir} — running auto_import")
        try:
            call_command('auto_import', verbosity=0)
        except Exception as e:
            logger.error(f"watcher: auto_import failed: {e}")
    if all_pdf_files:
        # Classify PDFs by content (not just filename)
        annual_files = []
        statement_files = []
        for f in all_pdf_files:
            full_path = os.path.join(inbox_dir, f)
            classification = _classify_pdf(full_path)
            if classification == 'annual':
                annual_files.append(f)
            else:
                statement_files.append(f)

        if annual_files:
            logger.info(f"watcher: found {len(annual_files)} Annual Report PDF(s) — running annual report import")
            try:
                call_command('manage_annual_report_import', inbox_dir=inbox_dir, verbosity=0)
            except Exception as e:
                logger.error(f"watcher: annual report import failed: {e}")
        if statement_files:
            logger.info(f"watcher: found {len(statement_files)} statement PDF(s) — running pdf import")
            try:
                call_command('manage_pdf_import', inbox_dir=inbox_dir, verbosity=0)
            except Exception as e:
                logger.error(f"watcher: pdf import failed: {e}")


def run_watcher_loop(interval: int, inbox_dir: str) -> None:
    """Poll inbox every *interval* seconds until the thread is killed.

    Runs once immediately on startup, then sleeps.
    """
    logger.info(f"watcher: starting — polling '{inbox_dir}' every {interval}s")
    _poll_once(inbox_dir)
    while True:
        time.sleep(interval)
        _poll_once(inbox_dir)


def start_background_watcher() -> None:
    """Start the watcher as a daemon thread.

    Reads IMPORT_INBOX_POLL_INTERVAL and IMPORT_INBOX_DIR from Django settings.
    Does nothing if the interval is 0 (disabled).
    """
    interval = int(getattr(settings, 'IMPORT_INBOX_POLL_INTERVAL', 300))
    if interval <= 0:
        logger.info("watcher: disabled (poll_interval=0 in config.ini)")
        return

    inbox_dir = str(getattr(settings, 'IMPORT_INBOX_DIR', settings.BASE_DIR / 'inbox'))

    thread = threading.Thread(
        target=run_watcher_loop,
        args=(interval, inbox_dir),
        name='inbox-watcher',
        daemon=True,   # dies automatically when the main process exits
    )
    thread.start()
    logger.info(f"watcher: daemon thread started (interval={interval}s, inbox={inbox_dir})")
