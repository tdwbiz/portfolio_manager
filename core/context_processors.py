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
