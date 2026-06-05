import os
import sys

# Ensure the project root is in sys.path
project_root = r'C:\AI-DevTeam\projects\portfolio_manager'
if project_root not in sys.path:
    sys.path.insert(0, project_root)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')

import django
django.setup()

from django.core.management import execute_from_command_line

if __name__ == '__main__':
    execute_from_command_line(['manage.py', 'test', 'risk.tests.test_security_metrics', '--settings=portfolio_manager.settings_test'])
