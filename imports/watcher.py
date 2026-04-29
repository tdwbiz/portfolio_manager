"""Shared inbox-watcher logic.

Used by both:
  - imports/apps.py  (AppConfig.ready — background daemon thread with runserver)
  - imports/management/commands/watch_inbox.py  (standalone management command)
"""

import logging
import os
import threading
import time

from django.conf import settings
from django.core.management import call_command

logger = logging.getLogger(__name__)


def _poll_once(inbox_dir: str) -> None:
    """Check inbox for CSVs and run auto_import if any are found."""
    if not os.path.isdir(inbox_dir):
        return
    csv_files = [f for f in os.listdir(inbox_dir) if f.lower().endswith('.csv')]
    if csv_files:
        logger.info(f"watcher: found {len(csv_files)} file(s) in {inbox_dir} — running auto_import")
        call_command('auto_import', verbosity=0)


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
