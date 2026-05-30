"""Views for PDF statement import functionality."""

import logging
from pathlib import Path

from django.shortcuts import render, redirect
from django.conf import settings
from decimal import Decimal

from core.models import Account, PdfStatement, PdfAccountBalance, PdfTransaction
from core.models import RbcAnnualReport, RbcAnnualBalances
from core.models import RbcAnnualPerformance, RbcAnnualPerformanceReturn
from core.models import RbcAnnualCharges, RbcAnnualCompensation
from imports.parsers_pdf import parse_rbc_pdf
from imports.parsers_annual import parse_rbc_annual_report

logger = logging.getLogger(__name__)


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
            if not uploaded_file.name.lower().endswith('.pdf'):
                context['error'] = 'Only PDF files are accepted.'
            else:
                try:
                    # Save uploaded file temporarily
                    upload_dir = Path(settings.MEDIA_ROOT) / 'pdf_uploads'
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    temp_path = upload_dir / uploaded_file.name
                    with open(temp_path, 'wb+') as destination:
                        for chunk in uploaded_file.chunks():
                            destination.write(chunk)

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
                            existing = PdfStatement.objects.filter(
                                account=account,
                                statement_date=statement_date,
                            )
                            if existing.exists():
                                context['error'] = (
                                    f'Duplicate statement: account={account}, '
                                    f'date={statement_date}'
                                )
                            else:
                                # Create PdfStatement
                                pdf_statement = PdfStatement.objects.create(
                                    account=account,
                                    file_name=uploaded_file.name,
                                    statement_date=statement_date,
                                    last_statement_date=parsed.get('last_statement_date'),
                                    account_number=account_number,
                                    fx_rate=parsed.get('fx_rate', Decimal('1.000000')),
                                )

                                # Create balances
                                for bal in parsed.get('balances', []):
                                    PdfAccountBalance.objects.create(
                                        pdf_statement=pdf_statement,
                                        account_type=bal['account_type'],
                                        opening_balance=bal.get('opening', Decimal('0.00')),
                                        closing_balance=bal.get('closing', Decimal('0.00')),
                                        market_value=bal.get('market_value', Decimal('0.00')),
                                        percentage=bal.get('percentage', Decimal('0.00')),
                                    )

                                # Create transactions
                                for tx in parsed.get('transactions', []):
                                    PdfTransaction.objects.create(
                                        pdf_statement=pdf_statement,
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
                                context['pdf_statement'] = pdf_statement
                except Exception as e:
                    logger.exception("Error processing PDF upload")
                    context['error'] = f'Error processing PDF: {e}'

    # Load existing PDF statements
    context['pdf_statements'] = PdfStatement.objects.select_related('account').all()

    return render(request, 'core/pdf_import.html', context)


def annual_report_import_view(request):
    """Handle RBC Annual Report upload and display results.

    GET: Show upload form + list of existing annual reports.
    POST: Handle file upload, parse PDF, create records, show results.
    """
    context = {}

    if request.method == 'POST':
        uploaded_file = request.FILES.get('pdf_file')
        if not uploaded_file:
            context['error'] = 'No file uploaded.'
        else:
            if not uploaded_file.name.lower().endswith('.pdf'):
                context['error'] = 'Only PDF files are accepted.'
            else:
                try:
                    # Save uploaded file temporarily
                    upload_dir = Path(settings.MEDIA_ROOT) / 'pdf_uploads'
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    temp_path = upload_dir / uploaded_file.name
                    with open(temp_path, 'wb+') as destination:
                        for chunk in uploaded_file.chunks():
                            destination.write(chunk)

                    # Parse the Annual Report PDF
                    parsed = parse_rbc_annual_report(str(temp_path))

                    # Look up or create Account
                    account = None
                    if parsed.get('account_number'):
                        try:
                            account = Account.objects.get(
                                account_id=parsed['account_number']
                            )
                        except Account.DoesNotExist:
                            account = None

                    if account is None:
                        # Try partial match
                        acct_num = parsed.get('account_number', '')
                        if acct_num:
                            partial = acct_num.replace('-', '')
                            for acct in Account.objects.filter(broker='RBC'):
                                if partial in acct.account_id.replace('-', ''):
                                    account = acct
                                    break

                    if account is None:
                        # Auto-create account
                        account = Account.objects.create(
                            account_id=parsed.get('account_number', f"RBC-{parsed.get('account_type_code', 'UNKNOWN')}"),
                            name=f"RBC {parsed.get('account_type_code', 'Account')} {parsed.get('account_number', '')}",
                            broker='RBC',
                        )
                        context['account_created'] = True

                    # Check for duplicate
                    existing = RbcAnnualReport.objects.filter(
                        account=account,
                        report_year=parsed['report_year'],
                    )
                    if existing.exists():
                        context['error'] = (
                            f'Duplicate annual report: account={account}, '
                            f'year={parsed["report_year"]}'
                        )
                    else:
                        # Create RbcAnnualReport
                        report = RbcAnnualReport.objects.create(
                            account=account,
                            broker='RBC',
                            file_name=uploaded_file.name,
                            report_year=parsed['report_year'],
                            report_period_start=parsed['report_period_start'],
                            report_period_end=parsed['report_period_end'],
                            account_number=parsed.get('account_number', ''),
                            account_type_code=parsed.get('account_type_code', ''),
                        )

                        # Create balances
                        for bal in parsed.get('balances', []):
                            RbcAnnualBalances.objects.create(
                                rbc_annual_report=report,
                                currency=bal['currency'],
                                beginning_market_value=bal['beginning_market_value'],
                                deposits_transfers_in=bal['deposits_transfers_in'],
                                withdrawals_transfers_out=bal['withdrawals_transfers_out'],
                                net_investment_return=bal['net_investment_return'],
                                ending_market_value=bal['ending_market_value'],
                                amount_invested=bal['amount_invested'],
                            )

                        # Create performance records
                        for perf in parsed.get('performance', []):
                            perf_record = RbcAnnualPerformance.objects.create(
                                rbc_annual_report=report,
                                currency=perf['currency'],
                                since_date=perf.get('since_date'),
                            )
                            for ret in perf.get('returns', []):
                                RbcAnnualPerformanceReturn.objects.create(
                                    performance=perf_record,
                                    period_label=ret['period_label'],
                                    return_pct=ret['return_pct'],
                                )

                        # Create charges
                        for charge in parsed.get('charges', []):
                            RbcAnnualCharges.objects.create(
                                rbc_annual_report=report,
                                currency=charge['currency'],
                                charge_category=charge['category'],
                                amount=charge['amount'],
                                is_subtotal=charge.get('is_subtotal', False),
                            )

                        # Create compensation
                        for comp in parsed.get('compensation', []):
                            RbcAnnualCompensation.objects.create(
                                rbc_annual_report=report,
                                currency=comp['currency'],
                                compensation_type=comp['type'],
                                amount=comp['amount'],
                            )

                        # Clean up temp file
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass

                        context['success'] = (
                            f'Successfully imported {uploaded_file.name}: '
                            f'year={parsed["report_year"]}, '
                            f'{len(parsed.get("balances", []))} balances, '
                            f'{len(parsed.get("performance", []))} performance records'
                        )
                        context['annual_report'] = report
                except Exception as e:
                    logger.exception("Error processing Annual Report upload")
                    context['error'] = f'Error processing Annual Report: {e}'

    # Load existing annual reports
    context['annual_reports'] = RbcAnnualReport.objects.select_related('account').all()

    return render(request, 'core/annual_report_import.html', context)
