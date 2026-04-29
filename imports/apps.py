import os
from django.apps import AppConfig


class ImportsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'imports'

    def ready(self) -> None:
        # Django's dev server forks — RUN_MAIN='true' is set only in the
        # reloader child (the live process). Skip in the parent to avoid
        # starting two watcher threads.
        if os.environ.get('RUN_MAIN') != 'true' and os.environ.get('DJANGO_SETTINGS_MODULE'):
            return

        from imports.watcher import start_background_watcher
        start_background_watcher()
