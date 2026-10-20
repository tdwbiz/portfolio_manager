# Deployment Guide

## Prerequisites

- Python 3.11+
- SQL Server (production) or SQLite (development)
- VL Model (optional, for annual report parsing)
- Amibroker (optional, for price imports)

## Environment Variables

```bash
# Database
DATABASE_URL=sqlserver+pyodbc://user:pass@server/dbname

# VL Model (optional — defaults to Halogen on port 11435 if unset)
VL_MODEL_BASE_URL=http://127.0.0.1:11435/v1
VL_MODEL_NAME=halogen-qwen3.8-flash-next

# Amibroker Watch Folder (optional)
AMIBROKER_WATCH_FOLDER=/path/to/amibroker_exports

# Django
SECRET_KEY=your-secret-key
DEBUG=False
ALLOWED_HOSTS=yourdomain.com
```

## Production Deployment Steps

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Settings

Copy `config.ini.example` to `config.ini` and update with production values.

### 3. Run Migrations

```bash
python manage.py migrate --settings=portfolio_manager.settings
```

### 4. Collect Static Files

```bash
python manage.py collectstatic --settings=portfolio_manager.settings
```

### 5. Seed Reference Data

```bash
python manage.py seed_rm_universe --settings=portfolio_manager.settings
python manage.py seed_instrument_catalogue --settings=portfolio_manager.settings
python manage.py seed_historical_scenarios --settings=portfolio_manager.settings
```

### 6. Fetch Initial Prices

```bash
python manage.py fetch_historical_prices --settings=portfolio_manager.settings
```

### 7. Start Application

Using Gunicorn:
```bash
gunicorn portfolio_manager.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

Using systemd:
```ini
[Unit]
Description=Portfolio Manager
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/path/to/portfolio_manager
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/venv/bin/gunicorn portfolio_manager.wsgi:application --bind 0.0.0.0:8000 --workers 4

[Install]
WantedBy=multi-user.target
```

## Monitoring

### Logs

```bash
tail -f logs/portfolio_manager.log
```

### Management Commands for Maintenance

```bash
# Refresh market data
python manage.py refresh_market_data

# Compute risk metrics
python manage.py calc_security_metrics

# Run stress test
python manage.py run_stress_test --scenario 2022_rate_shock

# Compute volatility
python manage.py compute_volatility
```

## Troubleshooting

### WeasyPrint Import Error

WeasyPrint requires system dependencies. Follow [installation instructions](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation).

### VL Model Connection Error

The app never requires `VL_MODEL_BASE_URL` to be set — if it is unset, the code falls back to the default (Halogen on `http://127.0.0.1:11435/v1`). A connection error means the model server is not running at the configured endpoint: check that Halogen is up on port 11435, or correct any `VL_MODEL_BASE_URL` / `VL_MODEL_NAME` overrides in `.env`.

### Database Connection Error

Verify `DATABASE_URL` is correctly formatted for SQL Server with pyodbc.
