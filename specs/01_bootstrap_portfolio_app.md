**Feature Spec: Bootstrap Django Portfolio Management App**

Project name: portfolio_manager
Customer: tdwdev
Database: Microsoft SQL Server (MSSQL) – use existing connection string or create new DB "PortfolioDev"
Django version: 5.1 (latest stable)

Requirements:
- Create a new Django project called "portfolio_manager"
- Create Django app called "core" and "imports"
- Configure MSSQL database connection (use mssql-django)
- Create models in core/models.py:
  - BrokerStatement (source: IB or RBC, file_date, raw_filename)
  - Transaction (all standard fields: date, symbol, type, quantity, price, commission, amount, currency, etc.)
- Basic settings: DEBUG=True, simple logging, .env support for DB credentials
- Use feature branch: feature/bootstrap-django-project
- Follow PEP8, add basic README.md with setup instructions

After this is done and merged to dev, the next step will be importing real IB and RBC statements into these tables.

Human approval required before any git push or DB changes.