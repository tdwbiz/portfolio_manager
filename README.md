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
