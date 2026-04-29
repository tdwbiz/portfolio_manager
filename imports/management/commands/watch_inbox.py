"""Django management command to watch inbox/ and auto-import new CSV files.

This is the standalone version for use in a dedicated terminal or cron job.
The same polling logic runs automatically inside runserver via ImportsConfig.ready().
"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from imports.watcher import run_watcher_loop

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Watch inbox/ for new CSV files and import them automatically. '
        'The same watcher starts automatically with runserver; '
        'use this command only when you need a standalone process.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=None,
            help='Polling interval in seconds (default: poll_interval from config.ini)',
        )

    def handle(self, *args, **options):
        interval = options['interval'] or int(getattr(settings, 'IMPORT_INBOX_POLL_INTERVAL', 300))
        inbox_dir = str(getattr(settings, 'IMPORT_INBOX_DIR', settings.BASE_DIR / 'inbox'))

        self.stdout.write(self.style.SUCCESS(
            f'Watching {inbox_dir} every {interval}s — press Ctrl+C to stop.'
        ))

        try:
            run_watcher_loop(interval, inbox_dir)
        except KeyboardInterrupt:
            self.stdout.write('\nWatcher stopped.')
