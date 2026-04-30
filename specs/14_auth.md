# Feature Spec: Authentication (Django Built-in Auth)

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/auth

---

## Context

Specs 01–07 are complete. All views are currently public — anyone with network access
can view the portfolio. This spec adds Django's built-in session authentication so that
all views require login, with a login page and a logout link in the nav.

No user registration — accounts are created only via `python manage.py createsuperuser`
(manual step). Django's `contrib.auth` is already in `INSTALLED_APPS` by default.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

1. **Update `portfolio_manager/settings.py`**
   - Add (or confirm already present): `'django.contrib.auth'` and
     `'django.contrib.contenttypes'` in `INSTALLED_APPS`.
   - Add `'django.contrib.auth.middleware.AuthenticationMiddleware'` to `MIDDLEWARE`
     (after `SessionMiddleware` — it is typically there by default; confirm it is).
   - Add at the bottom:
     ```python
     LOGIN_URL = '/login/'
     LOGIN_REDIRECT_URL = '/'
     LOGOUT_REDIRECT_URL = '/login/'
     ```

2. **Update `portfolio_manager/urls.py`**
   - Add Django's auth URLs:
     ```python
     from django.contrib.auth import views as auth_views

     urlpatterns = [
         ...existing paths...,
         path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
         path('logout/', auth_views.LogoutView.as_view(), name='logout'),
     ]
     ```

3. **Add `@login_required` to every view in `core/views.py`**
   - Import `from django.contrib.auth.decorators import login_required`.
   - Apply `@login_required` to: `dashboard`, `transaction_list`, `accounts_list`,
     `account_detail`, `account_create`, `account_edit`, `account_delete`,
     `portfolio_summary`, `realised_pnl` (if exists), `income_summary` (if exists),
     `import_history` (if exists), `download_rejection_log` (if exists),
     `portfolio_chart` (if exists), `portfolio_chart_data` (if exists),
     `transaction_export_csv` (if exists), `transaction_export_excel` (if exists).
   - Apply to all views that currently exist — do not error if some of the above don't exist yet.

4. **New login template `core/templates/core/login.html`**
   - Extends `core/base.html`.
   - Simple centered form:
     ```html
     <h1>Sign In</h1>
     <form method="post">
       {% csrf_token %}
       {{ form.as_p }}
       <button type="submit">Sign In</button>
     </form>
     {% if form.errors %}
     <p style="color:red">Invalid username or password.</p>
     {% endif %}
     ```
   - The base template nav links will still render — that is acceptable.

5. **Update `core/templates/core/base.html`**
   - Add a logout link to the nav, shown only when authenticated:
     ```html
     {% if user.is_authenticated %}
     <span style="float:right">
       {{ user.username }} &nbsp;|&nbsp;
       <a href="{% url 'logout' %}">Sign Out</a>
     </span>
     {% endif %}
     ```

6. **Tests in `core/tests/test_auth.py`**
   - `TestCase` class `AuthTests`.
   - `setUp`: create a test user:
     ```python
     from django.contrib.auth.models import User
     self.user = User.objects.create_user(username='testuser', password='testpass123')
     ```
   - `test_dashboard_redirects_unauthenticated`: GET `/` (unauthenticated) returns 302 and redirects to `/login/?next=/`.
   - `test_login_page_loads`: GET `/login/` returns 200.
   - `test_login_success`: POST to `/login/` with correct credentials redirects (302) to `/`.
   - `test_login_failure`: POST to `/login/` with wrong password returns 200 and contains `b'Invalid'`.
   - `test_authenticated_dashboard`: log in with `self.client.login(...)`, GET `/`, returns 200.
   - `test_logout`: log in, GET `/logout/`, redirects to `/login/`.
   - `test_accounts_list_requires_login`: GET `/accounts/` unauthenticated returns 302.

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations (Django's auth tables already exist from the initial
  `migrate` that was run when the project was set up).
- Do NOT add a registration view or sign-up form.
- The `@login_required` decorator must be on all views — do not use middleware-level
  auth that would also block the login page itself.
- Run `python manage.py test core --settings=portfolio_manager.settings_test`. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: Django auth — login required on all views"`

---

## Manual Steps Required

- [ ] On production server: `python manage.py createsuperuser` to create the admin account.
- [ ] Confirm `python manage.py migrate` has been run (auth tables must exist in PortfolioDEV).
- [ ] Review and push feature branch: `git push origin feature/auth`
- [ ] Merge to dev after review.
