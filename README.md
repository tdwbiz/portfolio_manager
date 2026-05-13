# Portfolio Manager

Modern, professional Django-based portfolio management application for tracking broker statements and transactions from Interactive Brokers (IB) and RBC Royal Bank.

## ✨ Features

- 📊 **Real-time Portfolio Tracking** with yfinance integration
- 💼 **Multi-broker Support** (Interactive Brokers, RBC)
- 📈 **Performance Charts** with historical price data
- 💰 **P&L Calculations** (Realised & Unrealised)
- 📥 **Automated Statement Import** with file watching
- 🎨 **Fully Customizable Branding** - colors, logos, themes
- 📱 **Modern Responsive UI** with Bootstrap 5
- 🔐 **Secure Admin Interface** for complete control

## Project Overview

This application provides a comprehensive solution for importing and managing financial transaction data from brokerage statements. The system is designed to:

- Store and track broker statements (IB or RBC sources)
- Parse and import transaction data automatically
- Calculate real-time portfolio values using Yahoo Finance
- Generate P&L reports with FIFO and average-cost methods
- Track income (dividends, interest)
- Visualize performance with interactive charts
- Provide customizable branding for professional deployment

## Tech Stack

- **Django**: 5.1.x (latest stable)
- **Database**: Microsoft SQL Server (MSSQL)
- **Python**: 3.11+
- **UI Framework**: Bootstrap 5 with responsive design
- **Price Data**: yfinance (Yahoo Finance API)
- **Charts**: Chart.js
- **Icons**: Bootstrap Icons

## Quick Start

### 1. Clone or Access the Project

```bash
cd C:\AI-DevTeam\projects\portfolio_manager
```

### 2. Create Virtual Environment

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the example environment file:
```bash
copy .env.example .env
```

Edit `.env` and fill in your database credentials (see `.env.example` for required variables)

### 5. Configure Database

Ensure your SQL Server database exists, or create it:

```sql
CREATE DATABASE PortfolioDev;
```

Update your `.env` file with the correct database credentials:

```
DB_HOST=localhost
DB_PORT=1433
DB_NAME=PortfolioDev
DB_USER=your_username
DB_PASSWORD=your_password
```

### 6. Run Migrations

```bash
python manage.py migrate
```

### 7. Create Superuser

```bash
python manage.py createsuperuser
```

### 8. Start Development Server

```bash
python manage.py runserver
```

Visit:
- **Application**: http://localhost:8000/
- **Admin Interface**: http://localhost:8000/admin/

## 📚 Documentation

- **[Admin & Branding Guide](ADMIN_BRANDING_GUIDE.md)** - Complete guide to customization, admin features, and branding configuration
- **Specs Directory** - Feature specifications in `specs/` folder

## Project Structure

portfolio_manager/
  core/                    # Core business logic and models
    models.py           # BrokerStatement, Transaction models
    admin.py            # Admin configuration
    urls.py             # App URLs
    views.py            # App views
  imports/                 # Statement import functionality
    models.py           # ImportJob model
    admin.py            # Admin configuration
    urls.py             # App URLs
    views.py            # App views
  portfolio_manager/      # Project settings
    settings.py         # Django settings
    urls.py             # Project URLs
    wsgi.py             # WSGI config
    asgi.py             # ASGI config
  manage.py               # Django management script
  requirements.txt        # Python dependencies
  .env.example            # Environment template
  README.md               # This file

## Models

### BrokerStatement

Represents a broker statement file:

Field          Type      Description
-------        ----      -----------
source         CharField 'IB' or 'RBC'
file_date      DateField Date on the statement
raw_filename   CharField Original filename
uploaded_at    DateTimeField Upload timestamp (auto)
processed      BooleanField Processing status
notes          TextField Additional notes

### Transaction

Represents a financial transaction:

Field          Type      Description
-------        ----      -----------
date           DateField Transaction date
symbol         CharField Stock ticker (e.g., AAPL)
type           CharField BUY, SELL, DIVIDEND, etc.
quantity       DecimalField Quantity of shares
price          DecimalField Price per share
commission     DecimalField Commission/fee
amount         DecimalField Total amount
currency       CharField Currency code
description    CharField Transaction description
broker_statement ForeignKey Source statement

## Environment Variables

Variable              Description                    Example
------                -----------                    -------
DJANGO_SECRET_KEY     Django secret key              your-secret-key-here
DB_HOST               SQL Server host                localhost
DB_PORT               SQL Server port                1433
DB_NAME               Database name                  PortfolioDB
DB_USER               Database username              your_username
DB_PASSWORD           Database password              your_password
DEBUG                 Debug mode                     True or False

## Next Steps

After merging to dev, the next phase will include:

1. Statement file upload functionality
2. CSV/Excel parser for IB and RBC formats
3. Transaction categorization and reconciliation
4. Portfolio value calculations
5. Reporting and analytics

## Development Workflow

1. Create feature branches from main
2. Follow PEP8 coding standards
3. Add tests for new functionality
4. Submit pull requests for review
5. Human approval required before:
   - Git pushes to remote repositories
   - Database schema changes
   - Production deployments

## Support

For issues or questions, contact the development team.

# Portfolio Manager

Django-based application for managing investment portfolios.

## Setup

1. **Create Virtual Environment**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   source venv/bin/activate  # Linux/Mac
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**
   Copy `.env.example` to `.env` and update database credentials:
   ```bash
   copy .env.example .env  # Windows
   cp .env.example .env    # Linux/Mac
   ```

4. **Run Migrations**
   ```bash
   python manage.py migrate
   ```

5. **Create Superuser**
   ```bash
   python manage.py createsuperuser
   ```

6. **Run Server**
   ```bash
   python manage.py runserver
   ```

## Project Structure

- `core/`: Core domain models (BrokerStatement, Transaction)
- `imports/`: Statement import logic (future)
- `portfolio_manager/`: Django project settings

## Database

Uses Microsoft SQL Server via `mssql-django`.
