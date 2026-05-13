# Portfolio Manager - Admin & Branding Guide

## Admin Section Access

The Portfolio Manager application includes a full Django admin interface for managing all aspects of the system.

### Accessing the Admin

1. **URL**: Navigate to `/admin/` (e.g., `http://localhost:8000/admin/`)
2. **Login**: Use your Django superuser credentials
3. **Quick Access**: If logged in as staff, click the "Admin" link in the navigation bar

### Creating a Superuser

If you don't have a superuser account yet:

```bash
python manage.py createsuperuser
```

Follow the prompts to create your admin account.

## Admin Features

The admin interface allows you to manage:

### 1. **Branding Configuration**
- Company name and logo
- Color schemes and themes
- Tagline and footer text
- Visual customization options

### 2. **Accounts**
- Create/edit brokerage accounts
- Set account names and currencies
- Configure starting balances

### 3. **Transactions**
- View all transactions
- Filter by type, date, account
- Search by symbol or notes
- Manually add/edit transactions

### 4. **Transaction Types**
- Manage transaction type definitions
- Configure which types affect positions/cash
- Add new transaction types as needed

### 5. **Broker Statements**
- View imported statements
- Track source files and import dates

### 6. **File Import Logs**
- Monitor import history
- Review success/error statuses
- Track records imported and skipped

## Branding Configuration

The Portfolio Manager supports complete visual customization through the admin interface.

### Configuring Your Brand

1. Go to `/admin/`
2. Click on **Branding Configuration**
3. Click on the existing configuration (or create if none exists)

### Branding Options

#### **Company Information**
- **Company Name**: Displayed in the navbar and page titles (default: "Portfolio Manager")
- **Company Logo URL**: Full URL to your logo image (PNG, SVG, or JPG recommended)
- **Tagline**: Optional slogan displayed below the navbar
- **Show Logo**: Toggle logo visibility
- **Show Tagline**: Toggle tagline visibility

#### **Color Scheme**
All colors use hex format (e.g., `#0d6efd`):

- **Primary Color**: Main brand color for buttons, highlights (default: `#0d6efd` - blue)
- **Secondary Color**: Supporting color for secondary elements (default: `#6c757d` - gray)
- **Success Color**: Positive values, gains (default: `#198754` - green)
- **Danger Color**: Negative values, losses (default: `#dc3545` - red)
- **Navbar Background**: Top navigation bar background (default: `#212529` - dark)
- **Navbar Text**: Top navigation bar text color (default: `#ffffff` - white)
- **Footer Background**: Bottom footer background (default: `#f8f9fa` - light gray)

#### **Footer**
- **Footer Text**: Copyright or custom footer message (supports HTML)

### Example Configuration

**Professional Corporate Look:**
```
Company Name: Acme Investments
Company Logo URL: https://example.com/logo.png
Tagline: Smart investing for the future
Primary Color: #1a237e (deep blue)
Secondary Color: #424242 (dark gray)
Success Color: #2e7d32 (forest green)
Danger Color: #c62828 (dark red)
Navbar Background: #1a237e (matches primary)
Navbar Text: #ffffff (white)
Footer Text: © 2026 Acme Investments. All rights reserved.
```

**Modern Tech Startup:**
```
Company Name: TechInvest
Primary Color: #6200ea (purple)
Secondary Color: #00e5ff (cyan)
Success Color: #00c853 (bright green)
Navbar Background: #000000 (black)
Tagline: Invest in tomorrow's technology today
```

## yfinance Price Integration

The Portfolio Manager uses **yfinance** (Yahoo Finance) to fetch real-time and historical stock prices.

### Features

1. **Real-time Prices**: Portfolio Summary page shows current market values
2. **Historical Prices**: Performance chart uses historical data for trend analysis
3. **Automatic Caching**: Prices are cached for 15 minutes to reduce API calls
4. **Multi-currency Support**: Automatically fetches USD/CAD exchange rates

### Implementation Details

Located in `core/utils.py`:

- **`fetch_last_prices(symbols)`**: Fetches current prices for portfolio positions
- **`compute_portfolio_value_series()`**: Fetches historical prices for performance charts
- **Caching**: Uses Django's cache framework to store prices temporarily

### Price Data Sources

- **Stock Prices**: Yahoo Finance API via yfinance
- **Currency Conversion**: USDCAD=X ticker for exchange rates
- **Cache Duration**: 15 minutes (900 seconds) for current prices, 1 hour for historical data

### Troubleshooting

If prices aren't loading:
1. Check internet connectivity
2. Verify stock symbols are valid
3. Check Django cache is configured (default is in-memory cache)
4. Review logs for yfinance errors

## Navigation & Features

### Main Menu Items

- **Dashboard**: Overview with accounts, recent transactions, and imports
- **Portfolio**: Current positions with real-time market values and P&L
- **Performance**: Historical portfolio value chart
- **Realised P&L**: Closed positions with FIFO and average-cost calculations
- **Income**: Dividend and interest income summary
- **Transactions**: Full transaction history with filtering
- **Accounts**: Account management (view/create/edit)
- **Import History**: Track imported broker statements
- **Admin**: Administrative interface (staff users only)

### User Permissions

- **Regular Users**: Can view data but cannot access admin
- **Staff Users**: Can access the admin interface
- **Superusers**: Full administrative access

## Modern UI Features

The application now includes:

✅ **Bootstrap 5**: Modern, responsive design framework
✅ **Bootstrap Icons**: Professional icon set throughout
✅ **Gradient Backgrounds**: Subtle, professional gradients
✅ **Card-based Layout**: Clean, organized information display
✅ **Responsive Design**: Works on desktop, tablet, and mobile
✅ **Color-coded Data**: Green for positive, red for negative values
✅ **Hover Effects**: Interactive elements with smooth transitions
✅ **Professional Typography**: Clean, modern font stack
✅ **Consistent Branding**: Company colors throughout the interface

## Best Practices

### Branding
- Use your company's official brand colors
- Keep logo files small (< 50KB) for fast loading
- Use transparent PNG or SVG for logos
- Test color combinations for readability

### Admin Security
- Use strong passwords for superuser accounts
- Don't share admin credentials
- Regularly review user permissions
- Enable HTTPS in production

### Performance
- Prices are cached automatically - no action needed
- Import files are processed asynchronously
- Database indexes optimize queries

## Support & Documentation

For technical issues or questions:
- Check `README.md` for setup instructions
- Review specs in the `specs/` directory
- Check Django logs for error messages
- Verify database connectivity

---

**Last Updated**: May 8, 2026
**Version**: 2.0 (Modern UI Update)
