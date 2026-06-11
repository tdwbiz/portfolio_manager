from django.conf import settings

from .models import BrandingConfig


def branding(request):
    """Add branding configuration to all templates."""
    return {
        'branding': BrandingConfig.load(),
        'app_settings': {
            'price_cache_timeout_minutes': settings.PRICE_CACHE_TIMEOUT_MINUTES,
        },
    }


def alert_badge(request):
    """Add unacknowledged alert count to all templates."""
    try:
        from risk.services.alerts import get_unacknowledged_alert_count
        count = get_unacknowledged_alert_count()
    except Exception:
        count = 0
    return {'unacknowledged_alert_count': count}
