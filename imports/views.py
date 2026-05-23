"""Views for PDF statement import functionality."""

import logging
from pathlib import Path

from django.shortcuts import render, redirect
from django.conf import settings
from decimal import Decimal

from core.models import Account, PdfStatement, PdfAccountBalance, PdfTransaction
from imports.parsers_pdf import parse_rbc_pdf

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
