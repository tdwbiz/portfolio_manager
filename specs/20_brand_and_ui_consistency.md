# Feature Spec: Brand Identity & UI Consistency

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/brand-ui-consistency

---

## Context

The application currently has a partially styled UI. Several pages (`accounts.html`,
`transaction_list.html`) are completely unstyled — bare HTML with no Bootstrap classes,
plain `<table>` tags, and plain `<a>` links. Other pages use Bootstrap well but
inconsistently: some use the `page-header` div pattern, some have bare `<h1>` tags,
some use inline styles. The `BrandingConfig` defaults are Django admin grey (`#417690`).

The customer has a brand identity: **TS Money — invest like you mean it**.
Assets are in `C:\AI-DevTeam\dev_crew\specs\logo\`:
- `xUs6Nlight banner.jpg` — full-width light banner (light blue gradient background, logo mark + "TS Money" wordmark + tagline)
- `8dDbV fav.jpg` — logo mark only (bars + arrow in circle, white background) — used as favicon and small icon

**Brand colour palette extracted from the logo:**

| Variable | Hex | Usage |
|---|---|---|
| `--ts-navy` | `#1A3F8B` | Navbar, primary buttons, headings, table headers |
| `--ts-green` | `#2FA02A` | Active nav links, success values, accent highlights |
| `--ts-light-bg` | `#EBF4FB` | Page background, banner area sides |
| `--ts-dark` | `#162F66` | Body text, sub-headings |
| `--ts-mid` | `#2D5FA0` | Secondary buttons, secondary accent |

This spec makes every page look like it belongs to the same professional application.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Bootstrap 5.3.2 (CDN), Bootstrap Icons (CDN)

---

## Requirements

### 1. Copy logo assets to static directory

Create directory `core/static/core/img/` and copy:

- `C:\AI-DevTeam\dev_crew\specs\logo\xUs6Nlight banner.jpg`
  → `core/static/core/img/tsmoney_banner.jpg`
- `C:\AI-DevTeam\dev_crew\specs\logo\8dDbV fav.jpg`
  → `core/static/core/img/tsmoney_icon.jpg`

Use shell commands (not Python) to copy:
```
copy "C:\AI-DevTeam\dev_crew\specs\logo\xUs6Nlight banner.jpg" "C:\AI-DevTeam\projects\portfolio_manager\core\static\core\img\tsmoney_banner.jpg"
copy "C:\AI-DevTeam\dev_crew\specs\logo\8dDbV fav.jpg" "C:\AI-DevTeam\projects\portfolio_manager\core\static\core\img\tsmoney_icon.jpg"
```

No new Django settings are needed — `django.contrib.staticfiles` already scans
each app's `static/` directory.

### 2. Create `core/static/core/css/tsmoney.css`

Create a new file with all brand CSS. **This replaces the inline `<style>` block in
`base.html`** — the inline block will be reduced to only the two lines that inject
dynamic CSS variables from the `BrandingConfig` model.

```css
/* ============================================================
   TS Money — Brand & UI Stylesheet
   Colours match the TS Money logo palette.
   ============================================================ */

/* ---- CSS custom properties (brand palette) ---- */
:root {
  --ts-navy:     #1A3F8B;
  --ts-green:    #2FA02A;
  --ts-light-bg: #EBF4FB;
  --ts-dark:     #162F66;
  --ts-mid:      #2D5FA0;
  --ts-white:    #FFFFFF;
  --ts-danger:   #C0392B;
  --ts-muted:    #6B7A8D;
  --ts-border:   #D0DFF0;
  --ts-shadow:   rgba(26, 63, 139, 0.10);
}

/* ---- Base ---- */
*, *::before, *::after { box-sizing: border-box; }

body {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  background-color: var(--ts-light-bg);
  color: var(--ts-dark);
}

/* ---- Navbar ---- */
.navbar {
  background-color: var(--ts-navy) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
  padding-top: 0.4rem;
  padding-bottom: 0.4rem;
}

.navbar-brand {
  font-weight: 700;
  font-size: 1.2rem;
  color: var(--ts-white) !important;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.navbar-brand img {
  height: 32px;
  width: 32px;
  object-fit: contain;
  border-radius: 4px;
}

.nav-link {
  color: rgba(255, 255, 255, 0.82) !important;
  font-size: 0.9rem;
  padding: 0.45rem 0.9rem !important;
  border-radius: 4px;
  transition: background-color 0.15s, color 0.15s;
}

.nav-link:hover {
  color: var(--ts-white) !important;
  background-color: rgba(255, 255, 255, 0.12);
}

.nav-link.active {
  color: var(--ts-white) !important;
  background-color: var(--ts-green) !important;
  font-weight: 600;
}

/* Dropdown menu */
.dropdown-menu {
  border: 1px solid var(--ts-border);
  border-radius: 8px;
  box-shadow: 0 4px 16px var(--ts-shadow);
  padding: 0.4rem;
}

.dropdown-item {
  border-radius: 5px;
  padding: 0.45rem 0.9rem;
  font-size: 0.9rem;
  color: var(--ts-dark);
}

.dropdown-item:hover {
  background-color: var(--ts-light-bg);
  color: var(--ts-navy);
}

/* ---- Site Banner ---- */
.site-banner {
  background-color: var(--ts-light-bg);
  display: flex;
  justify-content: center;
  align-items: center;
  border-bottom: 2px solid var(--ts-border);
  overflow: hidden;
  height: 90px;
}

.site-banner img {
  height: 90px;
  width: auto;
  max-width: 100%;
  object-fit: contain;
  display: block;
}

/* ---- Main content area ---- */
.main-content {
  flex: 1;
  padding: 1.75rem 0;
  background-color: var(--ts-light-bg);
}

/* ---- Page content card ---- */
.content-wrapper {
  background: var(--ts-white);
  border-radius: 10px;
  padding: 2rem;
  box-shadow: 0 2px 16px var(--ts-shadow);
  border-top: 3px solid var(--ts-navy);
}

/* ---- Page header ---- */
.page-header {
  border-bottom: 2px solid var(--ts-border);
  padding-bottom: 0.85rem;
  margin-bottom: 1.75rem;
}

.page-header h1 {
  color: var(--ts-navy);
  font-weight: 700;
  font-size: 1.6rem;
  margin: 0;
}

.page-header p {
  color: var(--ts-muted);
  font-size: 0.9rem;
  margin: 0.3rem 0 0 0;
}

/* ---- Buttons ---- */
.btn-primary {
  background-color: var(--ts-navy);
  border-color: var(--ts-navy);
  font-weight: 500;
}

.btn-primary:hover, .btn-primary:focus {
  background-color: var(--ts-mid);
  border-color: var(--ts-mid);
}

.btn-success {
  background-color: var(--ts-green);
  border-color: var(--ts-green);
  font-weight: 500;
}

.btn-success:hover {
  background-color: #259022;
  border-color: #259022;
}

.btn-outline-primary {
  color: var(--ts-navy);
  border-color: var(--ts-navy);
}

.btn-outline-primary:hover, .btn-outline-primary.active {
  background-color: var(--ts-navy);
  border-color: var(--ts-navy);
  color: var(--ts-white);
}

/* ---- Tables ---- */
.table {
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 1px 6px var(--ts-shadow);
  font-size: 0.9rem;
}

.table thead th {
  background-color: var(--ts-navy);
  color: var(--ts-white);
  font-weight: 600;
  border: none;
  padding: 0.75rem 1rem;
  white-space: nowrap;
}

.table tbody tr:nth-of-type(odd) {
  background-color: rgba(235, 244, 251, 0.5);
}

.table tbody tr:hover {
  background-color: rgba(26, 63, 139, 0.06);
}

.table td, .table th {
  vertical-align: middle;
  padding: 0.6rem 1rem;
}

/* ---- Cards ---- */
.card {
  border: 1px solid var(--ts-border);
  border-radius: 10px;
  box-shadow: 0 2px 8px var(--ts-shadow);
  transition: transform 0.18s, box-shadow 0.18s;
}

.card:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 18px var(--ts-shadow);
}

.card-header {
  background: linear-gradient(135deg, var(--ts-navy), var(--ts-mid));
  color: var(--ts-white);
  font-weight: 600;
  border-radius: 10px 10px 0 0 !important;
  border: none;
}

/* ---- Stat cards ---- */
.stat-card {
  text-align: center;
  padding: 1.4rem;
  border-top: 3px solid var(--ts-navy);
}

.stat-value {
  font-size: 2.2rem;
  font-weight: 700;
  color: var(--ts-navy);
  line-height: 1.1;
}

.stat-label {
  color: var(--ts-muted);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-top: 0.35rem;
}

/* ---- Positive / Negative values ---- */
.positive { color: var(--ts-green) !important; font-weight: 600; }
.negative { color: var(--ts-danger) !important; font-weight: 600; }

/* ---- Alerts ---- */
.alert {
  border-radius: 8px;
  border-left: 4px solid;
  border-top: none;
  border-right: none;
  border-bottom: none;
}

.alert-success  { border-left-color: var(--ts-green); }
.alert-danger   { border-left-color: var(--ts-danger); }
.alert-warning  { border-left-color: #E67E22; }
.alert-info     { border-left-color: var(--ts-mid); }

/* ---- Badge ---- */
.badge {
  padding: 0.45em 0.75em;
  font-weight: 500;
  font-size: 0.8em;
}

/* ---- Forms ---- */
.form-control:focus, .form-select:focus {
  border-color: var(--ts-mid);
  box-shadow: 0 0 0 0.2rem rgba(26, 63, 139, 0.18);
}

.form-label {
  font-weight: 500;
  color: var(--ts-dark);
  font-size: 0.9rem;
}

/* ---- Filter panel (used on transactions, P&L, income, chart pages) ---- */
.filter-panel {
  background: var(--ts-white);
  border: 1px solid var(--ts-border);
  border-radius: 8px;
  padding: 1.25rem 1.5rem;
  margin-bottom: 1.5rem;
  box-shadow: 0 1px 4px var(--ts-shadow);
}

.filter-panel label {
  font-weight: 500;
  font-size: 0.85rem;
  color: var(--ts-dark);
  margin-bottom: 0.25rem;
}

/* ---- Footer ---- */
footer {
  background-color: var(--ts-navy);
  color: rgba(255, 255, 255, 0.7);
  padding: 1rem 0;
  margin-top: auto;
  font-size: 0.85rem;
}

footer p { margin: 0; color: rgba(255, 255, 255, 0.7); }
footer a { color: rgba(255, 255, 255, 0.85); text-decoration: none; }
footer a:hover { color: var(--ts-white); }

/* ---- Login page ---- */
.login-card {
  border-top: 4px solid var(--ts-navy);
  border-radius: 12px;
  box-shadow: 0 8px 32px var(--ts-shadow);
}

.login-logo {
  width: 80px;
  height: 80px;
  object-fit: contain;
  display: block;
  margin: 0 auto 1rem;
}

/* ---- Responsive ---- */
@media (max-width: 768px) {
  .content-wrapper { padding: 1rem; }
  .stat-value { font-size: 1.8rem; }
  .site-banner { height: 60px; }
  .site-banner img { height: 60px; }
  .page-header h1 { font-size: 1.3rem; }
}
```

### 3. Update `core/templates/core/base.html`

Make the following changes (use `Replace string in file` for each targeted section):

**A. `<head>` — add favicon, load tsmoney.css, keep only dynamic vars inline**

Replace the entire `<style>` block and everything before `{% block extra_css %}` with:

```html
    <!-- Favicon -->
    {% load static %}
    <link rel="icon" type="image/jpeg" href="{% static 'core/img/tsmoney_icon.jpg' %}">

    <!-- Bootstrap 5 CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- Bootstrap Icons -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <!-- TS Money brand stylesheet -->
    <link rel="stylesheet" href="{% static 'core/css/tsmoney.css' %}">

    <style>
      /* Dynamic CSS variables from BrandingConfig — override brand defaults if admin changed them */
      :root {
        --primary-color: {{ branding.primary_color }};
        --secondary-color: {{ branding.secondary_color }};
        --success-color: {{ branding.success_color }};
        --danger-color: {{ branding.danger_color }};
        --navbar-bg: {{ branding.navbar_bg_color }};
        --navbar-text: {{ branding.navbar_text_color }};
        --footer-bg: {{ branding.footer_bg_color }};
      }
    </style>

    {% block extra_css %}{% endblock %}
```

Note: `{% load static %}` must appear **once** at the very top of base.html, before
the `<!DOCTYPE html>` line. Add it there if not already present.

**B. Add the site banner between the closing `</nav>` tag and the `<div class="main-content">` opening:**

```html
    <!-- Site Banner -->
    <div class="site-banner">
      <img src="{% static 'core/img/tsmoney_banner.jpg' %}" alt="TS Money — invest like you mean it">
    </div>
```

**C. Update `BrandingConfig` default colors in the navbar**

The `<nav>` tag currently uses `navbar-dark` and relies on `--navbar-bg` from
`BrandingConfig`. The existing class is fine — leave the `<nav>` tag unchanged.
The new CSS in `tsmoney.css` overrides `.navbar` with `--ts-navy` which takes
priority. No change needed to the nav HTML.

**D. Update footer HTML**

Replace the existing footer `<p>` content with:
```html
    <footer>
      <div class="container-fluid text-center">
        <p>
          {% if branding.footer_text %}{{ branding.footer_text }}{% else %}
          &copy; {% now "Y" %} TS Money &mdash; invest like you mean it
          {% endif %}
        </p>
      </div>
    </footer>
```

### 4. Update `BrandingConfig` default values via data migration

Create a new migration file `core/migrations/0XXX_update_branding_defaults.py`
(use the next available number after the last migration in the folder):

```python
from django.db import migrations

def update_branding_defaults(apps, schema_editor):
    BrandingConfig = apps.get_model('core', 'BrandingConfig')
    obj, _ = BrandingConfig.objects.get_or_create(pk=1)
    obj.company_name = 'TS Money'
    obj.tagline = 'invest like you mean it'
    obj.primary_color = '#1A3F8B'
    obj.secondary_color = '#2D5FA0'
    obj.success_color = '#2FA02A'
    obj.danger_color = '#C0392B'
    obj.navbar_bg_color = '#1A3F8B'
    obj.navbar_text_color = '#FFFFFF'
    obj.footer_bg_color = '#1A3F8B'
    obj.footer_text = '© TS Money — invest like you mean it'
    obj.show_logo = True
    obj.show_tagline = False   # tagline now lives in the banner image
    obj.save()

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0001_initial'),   # REPLACE with the actual last migration name
    ]
    operations = [
        migrations.RunPython(update_branding_defaults, migrations.RunPython.noop),
    ]
```

**IMPORTANT**: Before writing this migration, read the migrations folder
(`core/migrations/`) to find the actual last migration filename, and use that as the
dependency.

Run the migration against the test DB to verify it works:
```
venv\Scripts\python manage.py migrate --settings=portfolio_manager.settings_test
```
Then run it against PortfolioDEV:
```
venv\Scripts\python manage.py migrate
```

### 5. Rewrite `core/templates/core/accounts.html`

The current file is completely unstyled HTML (bare `<h1>`, plain `<table>`, no
Bootstrap). Replace the entire file content with a properly styled Bootstrap version:

```html
{% extends "core/base.html" %}
{% load static %}

{% block title %}Accounts — TS Money{% endblock %}

{% block content %}
<div class="page-header">
  <h1><i class="bi bi-bank"></i> Accounts</h1>
  <p>Manage your brokerage and investment accounts</p>
</div>

<div class="d-flex justify-content-end mb-3">
  <a href="{% url 'account_create' %}" class="btn btn-primary">
    <i class="bi bi-plus-circle"></i> Add Account
  </a>
</div>

{% if accounts %}
<div class="table-responsive">
  <table class="table table-striped table-hover align-middle">
    <thead>
      <tr>
        <th>Account ID</th>
        <th>Name</th>
        <th>Broker</th>
        <th>Currency</th>
        <th>Category</th>
        <th>Owner</th>
        <th class="text-end">Starting Balance</th>
        <th>Starting Date</th>
        <th class="text-end">Transactions</th>
        <th class="text-end">Total Invested (CAD)</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for account in accounts %}
      <tr>
        <td><code>{{ account.account_id }}</code></td>
        <td><strong>{{ account.name|default:"—" }}</strong></td>
        <td>{{ account.broker|default:"—" }}</td>
        <td><span class="badge bg-secondary">{{ account.currency }}</span></td>
        <td>{{ account.category|default:"—" }}</td>
        <td>{{ account.owner|default:"—" }}</td>
        <td class="text-end">${{ account.starting_balance|floatformat:2 }}</td>
        <td>{{ account.starting_date|default:"—" }}</td>
        <td class="text-end">{{ account.transaction_count|default:0 }}</td>
        <td class="text-end">${{ account.total_invested_cad|floatformat:2|default:"0.00" }}</td>
        <td>
          <a href="{% url 'account_detail' account.id %}" class="btn btn-sm btn-outline-primary me-1">
            <i class="bi bi-eye"></i>
          </a>
          <a href="{% url 'account_edit' account.id %}" class="btn btn-sm btn-outline-secondary me-1">
            <i class="bi bi-pencil"></i>
          </a>
          <a href="{% url 'account_delete' account.id %}" class="btn btn-sm btn-outline-danger">
            <i class="bi bi-trash"></i>
          </a>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<div class="alert alert-info">
  <i class="bi bi-info-circle"></i> No accounts found.
  <a href="{% url 'account_create' %}">Add your first account</a>.
</div>
{% endif %}
{% endblock %}
```

**IMPORTANT**: Before writing this template, read the current `accounts.html` to
confirm which context variables are available from the view (e.g. `accounts`,
`account.id`, `account.transaction_count`, `account.total_invested_cad`). Adjust
field names to match the actual view context exactly. Read `core/views.py` — the
`accounts_list` view — to verify.

### 6. Rewrite `core/templates/core/transaction_list.html` header and filter section

The current file uses bare HTML `<h1>`, `<form>`, `<label>`, `<select>`, `<input>`
with no Bootstrap. Apply Bootstrap 5 styling:

**Replace** the top of the file (from the start of `{% block content %}` through the
closing `</form>` of the filter form) with a styled version using:
- `<div class="page-header">` with `<h1>` and subtitle
- Filter controls inside a `<div class="filter-panel">` card
- `<select>` elements as `<select class="form-select form-select-sm">`
- Text inputs as `<input class="form-control form-control-sm">`
- Submit button as `<button class="btn btn-primary btn-sm">`
- A "Clear filters" link as `<a class="btn btn-outline-secondary btn-sm">`

**IMPORTANT**: Read the full current `transaction_list.html` before editing.
Use `Replace string in file` to replace only the top section (from `{% block content %}`
through the filter form `</form>`). Do NOT touch the table or pagination section.

### 7. Update `core/templates/core/login.html`

Replace the current logo section in the login card (the `company_logo_url` img + `<h2>`)
with the static icon image:

```html
        <div class="text-center mb-4">
          {% load static %}
          <img src="{% static 'core/img/tsmoney_icon.jpg' %}"
               alt="TS Money" class="login-logo">
          <h2 class="mb-1" style="color: var(--ts-navy); font-weight: 700;">TS Money</h2>
          <p class="text-muted small">invest like you mean it</p>
        </div>
```

Note: `{% load static %}` should only appear once per template. If it is already at
the top of the file, remove it from within the block.

### 8. Apply `page-header` consistency to remaining pages

Read each of the following templates. For any that have a bare `<h1>` outside a
`page-header` div, wrap it:

- `account_detail.html`
- `account_form.html`
- `import_history.html`
- `income_summary.html`
- `realised_pnl.html`
- `tax_summary.html`
- `portfolio_chart.html`

Pattern to apply where missing:
```html
<div class="page-header">
  <h1><i class="bi bi-ICON"></i> Page Title</h1>
  <p>Subtitle text</p>
</div>
```

Choose a relevant Bootstrap Icon for each page:
- account_detail / account_form: `bi-bank`
- import_history: `bi-upload`
- income_summary: `bi-piggy-bank`
- realised_pnl: `bi-cash-coin`
- tax_summary: `bi-receipt`
- portfolio_chart: `bi-graph-up-arrow`

For pages that already have a `page-header` div, verify the subtitle `<p>` has class
`text-muted` (not `tagline`) and update if needed.

### 9. Tests — `core/tests/test_branding.py` (new file)

```python
from django.test import TestCase, Client
from django.urls import reverse
from core.models import BrandingConfig

class BrandingMigrationTests(TestCase):
    def test_branding_config_loads(self):
        config = BrandingConfig.load()
        self.assertEqual(config.company_name, 'TS Money')
        self.assertEqual(config.primary_color, '#1A3F8B')

class BannerPresenceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(
            __import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model()
            .objects.create_user('testuser', password='testpass')
        )

    def _get(self, url_name):
        return self.client.get(reverse(url_name))

    def test_banner_on_dashboard(self):
        r = self._get('dashboard')
        self.assertContains(r, 'site-banner')
        self.assertContains(r, 'tsmoney_banner.jpg')

    def test_banner_on_portfolio(self):
        r = self._get('portfolio_summary')
        self.assertContains(r, 'site-banner')

    def test_favicon_in_head(self):
        r = self._get('dashboard')
        self.assertContains(r, 'tsmoney_icon.jpg')

    def test_tsmoney_css_linked(self):
        r = self._get('dashboard')
        self.assertContains(r, 'tsmoney.css')

    def test_accounts_page_has_page_header(self):
        r = self._get('accounts_list')
        self.assertContains(r, 'page-header')
        self.assertContains(r, 'btn btn-primary')

    def test_transaction_list_has_page_header(self):
        r = self._get('transaction_list')
        self.assertContains(r, 'page-header')
```

Run with: `venv\Scripts\python manage.py test core --settings=portfolio_manager.settings_test`

---

## Constraints

- Do NOT change any view logic, URL patterns, or models (except the data migration).
- Do NOT add any new Python packages.
- Do NOT alter the `BrandingConfig` model fields — only update the **data** via migration.
- `{% load static %}` must appear exactly **once** per template file, at the very top
  (before `{% extends %}`). If `{% extends %}` is present, `{% load static %}` must
  come before it — or, if that causes a Django error, place it immediately inside
  `{% block content %}` but only once. Check Django's rule: `{% load %}` tags must
  appear before they are used, but `{% extends %}` must be the very first tag.
  Correct order: `{% extends "..." %}` first, then `{% load static %}` at the top of
  the block that uses it.
- The `tsmoney.css` file uses CSS custom properties (`--ts-*`). These are separate
  from the `--primary-color` etc. set by the inline `<style>` block. Do NOT remove
  the inline block — the admin may have customised the `BrandingConfig` values.
- Do NOT remove the `branding` context variables from templates — they are still used
  in the `<style>` block and in `{% if branding.show_logo %}` conditionals.
- Follow PEP 8 for any Python. No trailing whitespace in HTML/CSS.
- Do NOT push to git — see Manual Steps.
- The chart template (`portfolio_chart.html`) has its own inline CSS for the controls
  panel. Do NOT remove that inline CSS — only ensure the page-header is present.

---

## Manual Steps Required

After the agent completes implementation:

- [ ] `venv\Scripts\python manage.py migrate --settings=portfolio_manager.settings_test`
- [ ] `venv\Scripts\python manage.py migrate` (apply branding data migration to PortfolioDEV)
- [ ] `venv\Scripts\python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] `venv\Scripts\python manage.py runserver`
- [ ] Open browser and verify:
  - [ ] Banner appears on every page (dashboard, portfolio, transactions, accounts, chart, P&L, income, tax summary)
  - [ ] Navbar is deep navy, active link is green
  - [ ] Footer is navy
  - [ ] Accounts page has Bootstrap table with action buttons
  - [ ] Transaction list has styled filter panel
  - [ ] Login page shows the logo mark icon + "TS Money" heading
  - [ ] All page titles use `page-header` pattern with icon
  - [ ] No bare unstyled `<h1>` tags on any page
  - [ ] Favicon shows in browser tab
- [ ] `git add -A && git commit -m "feat: TS Money brand identity, UI consistency, styled accounts and transaction pages"`
- [ ] `git push origin feature/brand-ui-consistency`

---

## Human Approval Required

Human approval required before any git push. Confirm the following before approving:
- Banner displays correctly on all pages
- No broken static file 404s in browser DevTools Network tab
- Login page shows logo and brand name
- All tests pass
