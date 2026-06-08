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


@register.filter
def quadrant_description(quadrant: str) -> str:
    """Human-readable quadrant description."""
    return {
        'Q1': 'Growth + Low Inflation',
        'Q2': 'Growth + High Inflation',
        'Q3': 'Contraction + Low Inflation',
        'Q4': 'Stagflation',
    }.get(quadrant, quadrant)


@register.filter
def pct(value, decimals=1):
    """Format decimal as percentage string."""
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "\u2014"

@register.filter
def intcomma(value):
    """Add commas to integer values: 1000 -> 1,000."""
    if value is None:
        return "\u2014"
    try:
        # Format with commas
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return str(value)