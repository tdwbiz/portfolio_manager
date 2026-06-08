"""Export view wrappers."""
from django.http import Http404

from risk.services.export import export_security_card_csv, export_security_card_pdf
from risk.services.export import export_strategy_report_pdf
from risk.views.helpers import get_latest_calc_date


def security_export_wrapper(request, symbol: str, fmt: str):
    """Wrapper for security export to avoid circular imports."""
    calc_date = get_latest_calc_date()
    if calc_date is None:
        raise Http404("No risk data available")

    if fmt == 'pdf':
        return export_security_card_pdf(symbol, calc_date)
    elif fmt == 'csv':
        return export_security_card_csv(symbol, calc_date)
    raise Http404


def strategy_export(request, rec_id: int, fmt: str):
    """Wrapper for strategy report export."""
    if fmt == 'pdf':
        return export_strategy_report_pdf(rec_id)
    raise Http404
