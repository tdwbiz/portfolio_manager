"""Views for PDF statement import functionality."""

import logging
from pathlib import Path

from django.shortcuts import render, redirect
from django.conf import settings
from decimal import Decimal

from core.models import Account, RbcMonthly, RbcMonthlyBalance, RbcMonthlyTransaction
from core.models import RbcAnnual
from imports.parsers_pdf import parse_rbc_pdf
from imports.parsers_annual_vl import parse_rbc_annual_report_vl

logger = logging.getLogger(__name__)


def save_uploaded_pdf(uploaded_file):
    """Save an uploaded PDF file to the MEDIA_ROOT/pdf_uploads directory.

    Validates that the file has a .pdf extension and contains valid PDF magic bytes (%PDF-).
    Returns the Path to the saved file, or raises ValueError if the file is not a PDF.
    """
    if not uploaded_file.name.lower().endswith('.pdf'):
        raise ValueError('Only PDF files are accepted.')

    upload_dir = Path(settings.MEDIA_ROOT) / 'pdf_uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / uploaded_file.name

    with open(temp_path, 'wb+') as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)

    # Validate PDF magic bytes
    with open(temp_path, 'rb') as f:
        header = f.read(5)
        if header != b'%PDF-':
            temp_path.unlink(missing_ok=True)
            raise ValueError('Uploaded file is not a valid PDF (invalid magic bytes).')

    return temp_path


def pdf_import_view(request):
    """Handle PDF statement upload and display results.

    GET: Show upload form + list of existing PDF statements.
    POST: Handle file upload, parse PDF, create records, show results.
    """
    context = {}

    if request.method == 'POST':
        uploaded_file = request.FILES.get('pdf_file')
        if not uploaded_file:
            context['error'] = 'No file uploaded.'
        else:
            try:
                temp_path = save_uploaded_pdf(uploaded_file)
            except ValueError as e:
                context['error'] = str(e)
            else:
                try:
                    # Parse the PDF
                    parsed = parse_rbc_pdf(str(temp_path))

                    statement_date = parsed.get('statement_date')
                    account_number = parsed.get('account_number', '')

                    if not statement_date:
                        context['error'] = 'Could not determine statement date from PDF.'
                    else:
                        # Look up or create Account
                        account = None
                        if account_number:
                            try:
                                account = Account.objects.get(account_id=account_number)
                            except Account.DoesNotExist:
                                account = None

                        if account is None:
                            context['error'] = (
                                f'No account found for account_number="{account_number}". '
                                f'Please create the account first.'
                            )
                        else:
                            # Check for duplicate
                            existing = RbcMonthly.objects.filter(
                                account=account,
                                statement_date=statement_date,
                            )
                            if existing.exists():
                                context['error'] = (
                                    f'Duplicate statement: account={account}, '
                                    f'date={statement_date}'
                                )
                            else:
                                # Create RbcMonthly
                                rbc_monthly = RbcMonthly.objects.create(
                                    account=account,
                                    file_name=uploaded_file.name,
                                    statement_date=statement_date,
                                    last_statement_date=parsed.get('last_statement_date'),
                                    account_number=account_number,
                                    fx_rate=parsed.get('fx_rate', Decimal('1.000000')),
                                    begin_total_market_value=parsed.get('begin_total_market_value', Decimal('0.00')),
                                    end_total_market_value=parsed.get('end_total_market_value', Decimal('0.00')),
                                )

                                # Create balances
                                for bal in parsed.get('balances', []):
                                    RbcMonthlyBalance.objects.create(
                                        rbc_monthly=rbc_monthly,
                                        account_type=bal['account_type'],
                                        opening_balance=bal.get('opening', Decimal('0.00')),
                                        closing_balance=bal.get('closing', Decimal('0.00')),
                                        market_value=bal.get('market_value', Decimal('0.00')),
                                        percentage=bal.get('percentage', Decimal('0.00')),
                                    )

                                # Create transactions
                                for tx in parsed.get('transactions', []):
                                    RbcMonthlyTransaction.objects.create(
                                        rbc_monthly=rbc_monthly,
                                        date=tx['date'],
                                        activity_type=tx['activity_type'],
                                        description=tx['description'],
                                        symbol=tx.get('symbol', ''),
                                        quantity=tx.get('quantity', Decimal('0.00')),
                                        price=tx.get('price', Decimal('0.00')),
                                        debit=tx.get('debit', Decimal('0.00')),
                                        credit=tx.get('credit', Decimal('0.00')),
                                        currency=tx.get('currency', 'CAD'),
                                        commission=tx.get('commission', Decimal('0.00')),
                                    )

                                # Clean up temp file
                                try:
                                    temp_path.unlink()
                                except OSError:
                                    pass

                                context['success'] = (
                                    f'Successfully imported {uploaded_file.name}: '
                                    f'{len(parsed.get("transactions", []))} transactions, '
                                    f'{len(parsed.get("balances", []))} balances'
                                )
                                context['rbc_monthly'] = rbc_monthly
                except Exception as e:
                    logger.exception("Error processing PDF upload")
                    context['error'] = f'Error processing PDF: {e}'

    # Load existing PDF statements
    context['rbc_monthly_statements'] = RbcMonthly.objects.select_related('account').all()

    return render(request, 'core/pdf_import.html', context)


def annual_report_import_view(request):
    """Handle RBC Annual Report upload and display results.

    GET: Show upload form + list of existing annual reports.
    POST: Handle file upload, parse PDF via VL model, create flat RbcAnnual rows, show results.
    """
    context = {}

    if request.method == 'POST':
        uploaded_file = request.FILES.get('pdf_file')
        if not uploaded_file:
            context['error'] = 'No file uploaded.'
        else:
            try:
                temp_path = save_uploaded_pdf(uploaded_file)
            except ValueError as e:
                context['error'] = str(e)
            else:
                try:
                    # Parse the Annual Report PDF via VL model (hard error on failure)
                    parsed = parse_rbc_annual_report_vl(str(temp_path))

                    # Look up or create Account
                    account = None
                    if parsed.get('account_number'):
                        try:
                            account = Account.objects.get(account_id=parsed['account_number'])
                        except Account.DoesNotExist:
                            account = None

                    if account is None:
                        acct_num = parsed.get('account_number', '')
                        if acct_num:
                            partial = acct_num.replace('-', '')
                            for acct in Account.objects.filter(broker='RBC'):
                                if partial in acct.account_id.replace('-', ''):
                                    account = acct
                                    break

                    if account is None:
                        account = Account.objects.create(
                            account_id=parsed.get('account_number', f"RBC-{parsed.get('account_type_code', 'UNKNOWN')}"),
                            name=f"RBC {parsed.get('account_type_code', 'Account')} {parsed.get('account_number', '')}",
                            broker='RBC',
                        )
                        context['account_created'] = True

                    records_created = []
                    for currency, cdata in [('CAD', parsed.get('cad')), ('USD', parsed.get('usd'))]:
                        if cdata is None:
                            continue
                        existing = RbcAnnual.objects.filter(
                            account=account,
                            report_year=parsed['report_year'],
                            currency=currency,
                        )
                        if existing.exists():
                            context['error'] = (
                                f'Duplicate annual record: account={account}, '
                                f'year={parsed["report_year"]}, currency={currency}'
                            )
                            break
                        rec = RbcAnnual.objects.create(
                            account=account,
                            file_name=uploaded_file.name,
                            report_year=parsed['report_year'],
                            report_period_start=parsed['report_period_start'],
                            report_period_end=parsed['report_period_end'],
                            account_number=parsed.get('account_number', ''),
                            account_type_code=parsed.get('account_type_code', ''),
                            currency=currency,
                            bal_beginning_value=cdata.get('bal_beginning_value', 0),
                            bal_deposits_in=cdata.get('bal_deposits_in', 0),
                            bal_withdrawals_out=cdata.get('bal_withdrawals_out', 0),
                            bal_net_return=cdata.get('bal_net_return', 0),
                            bal_ending_value=cdata.get('bal_ending_value', 0),
                            bal_amount_invested=cdata.get('bal_amount_invested', 0),
                            perf_since_date=cdata.get('perf_since_date'),
                            perf_1yr=cdata.get('perf_1yr'),
                            perf_3yr=cdata.get('perf_3yr'),
                            perf_5yr=cdata.get('perf_5yr'),
                            perf_10yr=cdata.get('perf_10yr'),
                            perf_since=cdata.get('perf_since'),
                            chg_operating=cdata.get('chg_operating', 0),
                            chg_commissions=cdata.get('chg_commissions', 0),
                            chg_total_transaction=cdata.get('chg_total_transaction', 0),
                            chg_total_paid=cdata.get('chg_total_paid', 0),
                            comp_trailing=cdata.get('comp_trailing', 0),
                            comp_other=cdata.get('comp_other', 0),
                            comp_total=cdata.get('comp_total', 0),
                        )
                        records_created.append(rec)

                    if 'error' not in context and records_created:
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass
                        context['success'] = (
                            f'Successfully imported {uploaded_file.name}: '
                            f'year={parsed["report_year"]}, '
                            f'{len(records_created)} currency record(s) created'
                        )
                        context['annual_records'] = records_created
                except Exception as e:
                    logger.exception("Error processing Annual Report upload")
                    context['error'] = f'Error processing Annual Report: {e}'

    # Load existing annual reports
    context['annual_reports'] = RbcAnnual.objects.select_related('account').order_by('-report_year', 'account', 'currency')

    return render(request, 'core/annual_report_import.html', context)
