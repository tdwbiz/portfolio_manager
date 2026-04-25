# Portfolio Manager

A Django application for managing investment portfolios.

## Setup Instructions

1. **Create Virtual Environment**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # On Windows
   ```

2. **Install Dependencies**
   ```bash
   pip install django==5.1 mssql-django python-dotenv
   ```

3. **Configure Database**
   - Copy `.env.example` to `.env` (if exists) or edit the existing `.env` file.
   - Update the database credentials (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`) to match your MSSQL server.

4. **Run Migrations**
   ```bash
   python manage.py migrate
   ```

5. **Create Superuser**
   ```bash
   python manage.py createsuperuser
   ```

6. **Run Development Server**
   ```bash
   python manage.py runserver
   ```

## Project Structure

- `core/`: Core business logic and models (BrokerStatement, Transaction).
- `imports/`: App for handling statement imports (IB, RBC).
- `portfolio_manager/`: Django project settings and configuration.

## License

MIT
