# Feature Spec: Brand Identity & UI Consistency

**Spec:** D5 — Spec 20
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Goal

Apply a consistent visual identity across all pages using CSS variables, standardised page headers, utility classes, and proper branding integration via the `BrandingConfig` model.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Bootstrap 5, Chart.js (already loaded)
- New: `core/static/core/css/tsmoney.css`

---

## Requirements

### 1. Copy logo assets to static directory

Ensure logo files exist at `core/static/core/img/`:
- `logo.png` — primary logo
- `favicon.png` — favicon icon

If they don't exist yet, create placeholder files or skip (the spec expects them to be configurable via admin, so the path just needs to be correct).

### 2. Create `core/static/core/css/tsmoney.css`

Create a comprehensive CSS file with design tokens and utility classes.

**CSS Variables (`:root`):**
```css
:root {
    --primary-color: {{ primary_color|default:'#417690' }};
    --secondary-color: {{ secondary_color|default:'#447e9b' }};
    --success-color: {{ success_color|default:'#28a745' }};
    --danger-color: {{ danger_color|default:'#ba2121' }};
    --navbar-bg: {{ navbar_color|default:'#343a40' }};
    --footer-bg: {{ footer_color|default:'#343a40' }};
}
```

**Global resets:**
```css
*, *::before, *::after { box-sizing: border-box; }

body {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
}
```

**Navbar styling:**
```css
.navbar {
    background-color: var(--navbar-bg) !important;
}
.navbar-brand img {
    max-height: 32px;
}
.nav-link {
    color: rgba(255,255,255,0.85) !important;
    transition: color 0.15s;
}
.nav-link:hover {
    color: #fff !important;
}
.nav-link.active {
    color: #fff !important;
    font-weight: 600;
}
```

**Dropdown menu:**
```css
.dropdown-menu {
    border: none;
    box-shadow: 0 0.125rem 0.25rem rgba(0,0,0,0.075);
}
.dropdown-item:hover {
    background-color: var(--primary-color);
    color: #fff;
}
```

**Site banner:**
```css
.site-banner {
    text-align: center;
    padding: 0.5rem 1rem;
    background-color: var(--primary-color);
    color: #fff;
}
.site-banner img {
    max-height: 24px;
    vertical-align: middle;
    margin-right: 0.5rem;
}
```

**Main content wrapper:**
```css
.main-content {
    flex: 1 0 auto;
}
.content-wrapper {
    max-width: 1200px;
    margin: 0 auto;
    padding: 1.5rem 1rem;
    width: 100%;
}
```

**Page headers:**
```css
.page-header {
    margin-bottom: 1.5rem;
}
.page-header h1 {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 0.25rem;
}
.page-header p {
    color: #6c757d;
    font-size: 0.9rem;
    margin: 0;
}
```

**Buttons:**
```css
.btn-primary {
    background-color: var(--primary-color);
    border-color: var(--primary-color);
}
.btn-primary:hover, .btn-primary:focus {
    background-color: var(--secondary-color);
    border-color: var(--secondary-color);
}
.btn-success {
    background-color: var(--success-color);
    border-color: var(--success-color);
}
.btn-success:hover {
    background-color: #218838;
    border-color: #1e7e34;
}
.btn-outline-primary {
    color: var(--primary-color);
    border-color: var(--primary-color);
}
.btn-outline-primary:hover, .btn-outline-primary.active {
    background-color: var(--primary-color);
    border-color: var(--primary-color);
}
```

**Tables:**
```css
.table {
    font-size: 0.875rem;
}
.table thead th {
    border-bottom: 2px solid var(--primary-color);
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.025em;
}
.table tbody tr:nth-of-type(odd) {
    background-color: rgba(0,0,0,0.02);
}
.table tbody tr:hover {
    background-color: rgba(0,0,0,0.05);
}
.table td, .table th {
    padding: 0.5rem;
    vertical-align: middle;
}
```

**Cards:**
```css
.card {
    border: 1px solid rgba(0,0,0,0.125);
    border-radius: 0.375rem;
    transition: box-shadow 0.15s;
}
.card:hover {
    box-shadow: 0 0.125rem 0.25rem rgba(0,0,0,0.075);
}
.card-header {
    background-color: rgba(0,0,0,0.02);
    border-bottom: 1px solid rgba(0,0,0,0.125);
    font-weight: 600;
}
```

**Stat cards:**
```css
.stat-card {
    border-left: 3px solid var(--primary-color);
}
.stat-value {
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--primary-color);
}
.stat-label {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #6c757d;
}
```

**Utility classes:**
```css
.positive { color: var(--success-color) !important; }
.negative { color: var(--danger-color) !important; }
```

**Alerts:**
```css
.alert {
    border-radius: 0.375rem;
    border-left: 3px solid;
}
.alert-success { border-left-color: var(--success-color); }
.alert-danger { border-left-color: var(--danger-color); }
.alert-warning { border-left-color: #ffc107; }
.alert-info { border-left-color: #17a2b8; }
```

**Badges:**
```css
.badge {
    font-weight: 500;
    padding: 0.35em 0.65em;
}
```

**Forms:**
```css
.form-control:focus, .form-select:focus {
    border-color: var(--primary-color);
    box-shadow: 0 0 0 0.2rem rgba(65,118,144,0.25);
}
.form-label {
    font-weight: 500;
    font-size: 0.85rem;
}
```

**Filter panel:**
```css
.filter-panel {
    background-color: rgba(0,0,0,0.02);
    border: 1px solid rgba(0,0,0,0.125);
    border-radius: 0.375rem;
    padding: 1rem;
    margin-bottom: 1rem;
}
.filter-panel label {
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.025em;
    color: #6c757d;
    margin-bottom: 0.25rem;
}
```

**Footer:**
```css
footer {
    flex-shrink: 0;
    background-color: var(--footer-bg);
    color: rgba(255,255,255,0.7);
    padding: 1rem 0;
    font-size: 0.8rem;
}
footer p { margin: 0; }
footer a {
    color: rgba(255,255,255,0.7);
    text-decoration: none;
}
footer a:hover {
    color: #fff;
    text-decoration: underline;
}
```

**Login card:**
```css
.login-card {
    max-width: 400px;
    margin: 2rem auto;
}
.login-logo {
    max-height: 48px;
    margin-bottom: 1rem;
}
```

**Responsive breakpoints:**
```css
@media (max-width: 768px) {
    .content-wrapper { padding: 1rem 0.5rem; }
    .stat-value { font-size: 1.25rem; }
    .site-banner { padding: 0.25rem 0.5rem; }
    .site-banner img { max-height: 18px; }
    .page-header h1 { font-size: 1.25rem; }
}
```

### 3. Update `core/templates/core/base.html`

**A.** Add CSS includes in `<head>`:
```html
<link rel="stylesheet" href="{% static 'core/css/tsmoney.css' %}">
<link rel="icon" type="image/png" href="{% static 'core/img/favicon.png' %}">
```

**B.** Add CSS variables based on BrandingConfig:
```html
<style>
:root {
    --primary-color: {{ brand.primary_color|default:'#417690' }};
    --secondary-color: {{ brand.secondary_color|default:'#447e9b' }};
    --success-color: {{ brand.success_color|default:'#28a745' }};
    --danger-color: {{ brand.danger_color|default:'#ba2121' }};
    --navbar-bg: {{ brand.navbar_color|default:'#343a40' }};
    --footer-bg: {{ brand.footer_color|default:'#343a40' }};
}
</style>
```

**C.** Add site banner (conditional on `brand.show_banner`):
```html
{% if brand.show_banner %}
<div class="site-banner">
    {% if brand.logo_url %}
    <img src="{{ brand.logo_url }}" alt="Logo">
    {% endif %}
    <span>{{ brand.tagline }}</span>
</div>
{% endif %}
```

**D.** Wrap main content in proper wrapper:
```html
<div class="main-content">
    <div class="content-wrapper">
        {% block content %}{% endblock %}
    </div>
</div>
```

**E.** Add footer:
```html
<footer>
    <div class="content-wrapper">
        <p>&copy; {% now "Y" %} {{ brand.company_name|default:'Portfolio Manager' }}.
        <a href="{% url 'admin:index' %}">Admin</a></p>
    </div>
</footer>
```

### 4. Update templates to use `.page-header`

Add `.page-header` div at the top of every page that currently has an `<h1>`:

**`dashboard.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-speedometer2"></i> Dashboard</h1>
    <p>Portfolio overview and recent activity</p>
</div>
```

**`accounts.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-wallet2"></i> Accounts</h1>
    <p>Manage your brokerage accounts</p>
</div>
```

**`transaction_list.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-list-ul"></i> Transactions</h1>
    <p>View and filter all transactions</p>
</div>
```

**`portfolio.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-graph-up"></i> Portfolio Summary</h1>
    <p>Current holdings and performance</p>
</div>
```

**`portfolio_chart.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-bar-chart-line"></i> Performance Chart</h1>
    <p>Portfolio value over time</p>
</div>
```

**`realised_pnl.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-cash-stack"></i> Realised P&L</h1>
    <p>Capital gains and losses by trade</p>
</div>
```

**`income_summary.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-coin"></i> Dividend & Income Summary</h1>
    <p>Tax-relevant income by symbol and year</p>
</div>
```

**`tax_summary.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-file-earmark-text"></i> Annual Tax Summary</h1>
    <p>Consolidated tax report for the selected year</p>
</div>
```

**`import_history.html` — wrap existing `<h1>`:**
```html
<div class="page-header">
    <h1><i class="bi bi-clock-history"></i> Import History</h1>
    <p>View import runs and download rejection logs</p>
</div>
```

### 5. Update `core/templates/core/login.html`

Add logo and styling:
```html
<div class="login-card card">
    <div class="card-body">
        {% if brand.logo_url %}
        <div class="text-center mb-3">
            <img src="{{ brand.logo_url }}" alt="Logo" class="login-logo">
        </div>
        {% endif %}
        <h2 class="text-center mb-3">Sign In</h2>
        <form method="post">
            {% csrf_token %}
            {{ form.as_p }}
            <button type="submit" class="btn btn-primary w-100">Sign In</button>
        </form>
        {% if form.errors %}
        <div class="alert alert-danger mt-3">Invalid username or password.</div>
        {% endif %}
    </div>
</div>
```

### 6. Tests — `core/tests/test_branding.py` (new file)

```python
from django.test import TestCase, Client
from django.urls import reverse
from core.models import BrandingConfig
from django.contrib.auth.models import User


class BrandingConfigTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.brand = BrandingConfig.objects.create(
            company_name='Test Portfolio',
            primary_color='#417690',
            secondary_color='#447e9b',
            success_color='#28a745',
            danger_color='#ba2121',
        )
        self.user = User.objects.create_user('testuser', 'test@test.com', 'testpass')

    def test_branding_config_loads(self):
        """BrandingConfig is available in template context."""
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Portfolio')

    def test_favicon_in_head(self):
        """Favicon link is present in page <head>."""
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'favicon.png')

    def test_tsmoney_css_linked(self):
        """tsmoney.css is linked in page <head>."""
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'tsmoney.css')

    def test_page_header_on_dashboard(self):
        """Dashboard has .page-header element."""
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'page-header')

    def test_page_header_on_accounts(self):
        """Accounts page has .page-header element."""
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('accounts_list'))
        self.assertContains(response, 'page-header')

    def test_page_header_on_transaction_list(self):
        """Transaction list page has .page-header element."""
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('transaction_list'))
        self.assertContains(response, 'page-header')

    def test_login_page_has_logo_styling(self):
        """Login page uses login-card class."""
        response = self.client.get(reverse('login'))
        self.assertContains(response, 'login-card')

    def test_site_banner_shows_when_enabled(self):
        """Banner appears when show_banner is True."""
        self.brand.show_banner = True
        self.brand.tagline = 'Test Tagline'
        self.brand.save()
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Test Tagline')
```

---

## Constraints

- Do NOT change any model fields or create migrations (CSS/template only)
- Bootstrap 5 must remain the base framework — this CSS enhances, does not replace
- CSS variables must fall back to hardcoded defaults if BrandingConfig is missing
- Favicon path must not break if the file doesn't exist (HTML is valid either way)
- No new Python packages required
- Follow PEP 8. Add type hints to any new view/context logic.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify every page uses consistent `.page-header` styling
- [ ] Manual browser test: verify logo, banner, and footer render correctly
- [ ] Manual browser test: verify colours match BrandingConfig settings
- [ ] `git add -A && git commit -m "feat: brand identity and UI consistency across all pages"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push.
