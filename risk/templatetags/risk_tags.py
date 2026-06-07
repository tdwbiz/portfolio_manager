from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Allow dict lookups with variable keys in templates: {{ dict|get_item:key }}"""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def multiply(value, arg):
    """Multiply filter for template width calculations."""
    try:
        return int(value) * int(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def urgency_colour(level: str) -> str:
    """Return Bootstrap colour class for an urgency level."""
    return {
        'Critical': 'danger',
        'High': 'warning',
        'Medium': 'info',
        'Low': 'success',
    }.get(level, 'secondary')
