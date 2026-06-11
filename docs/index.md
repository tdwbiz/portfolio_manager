# Portfolio Manager

A Django-based investment portfolio management system with risk analytics, Monte Carlo simulations, and automated PDF statement imports.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Run tests
python manage.py test --settings=portfolio_manager.settings_test

# Start development server
python manage.py runserver
```

## Project Structure

```
portfolio_manager/
├── core/              # Core Django app (accounts, transactions, portfolios)
├── imports/           # PDF statement import pipeline
├── risk/              # Risk analytics engine
├── portfolio_manager/ # Django project settings
├── scripts/           # Utility scripts
├── specs/             # Feature specifications
└── docs/              # This documentation
```

## Key Features

- **Portfolio Tracking**: Multi-account, multi-currency portfolio management
- **Risk Analytics**: VaR, CVaR, Monte Carlo simulations, stress testing
- **Technical Indicators**: RSI, MACD, Bollinger Bands, SMA crossovers
- **PDF Imports**: Automated parsing of RBC and Interactive Brokers statements
- **Backtesting**: Historical scenario testing with strategy overlays
- **Option Pricing**: Black-Scholes model with Greeks

## Documentation

- [Architecture](architecture.md) — System design and data flow
- [API Reference](api/models.md) — Models, services, and views
- [Deployment](deployment.md) — Production deployment guide

## Testing

```bash
# Run all tests
python manage.py test --settings=portfolio_manager.settings_test

# Run specific app
python manage.py test risk.tests --settings=portfolio_manager.settings_test
```

## License

Private — All rights reserved.
