# Development Guide

## Quick Start

```powershell
# Start dev server
&C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py runserver --settings=portfolio_manager.settings

# Run tests
&C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py test core --settings=portfolio_manager.settings_test
```

## Virtual Environment

**There is only ONE virtual environment: `ai-env`.** Do NOT create or use any other venv.

```powershell
# Activate ai-env
&C:\AI-DevTeam\ai-env\Scripts\Activate.ps1
# or just
activate
```

> The old `venv/` folder has been removed. Always use `ai-env`.

## Settings Explained

Django uses `settings.py` for production and `settings_test.py` for testing.

| File | Database | Purpose |
|------|----------|---------|
| `portfolio_manager/settings.py` | MSSQL (production) | Live server, development against real data |
| `portfolio_manager/settings_test.py` | SQLite in-memory | All tests — creates fresh DB per run, never touches production data |

### When to use which

| Task | Use | Command |
|------|-----|---------|
| Run dev server | `settings.py` | `manage.py runserver --settings=portfolio_manager.settings` |
| Run tests | `settings_test.py` | `manage.py test core --settings=portfolio_manager.settings_test` |
| Create migrations | `settings_test.py` | `manage.py makemigrations --settings=portfolio_manager.settings_test` |
| Check migrations | `settings_test.py` | `manage.py makemigrations --check --settings=portfolio_manager.settings_test` |
| Apply migrations (test DB) | `settings_test.py` | `manage.py migrate --settings=portfolio_manager.settings_test` |
| Apply migrations (prod) | `settings.py` | `manage.py migrate --settings=portfolio_manager.settings` |

**Why `settings_test.py` for migrations?**
- `makemigrations` reads model definitions, not the database — so SQLite is fine
- `migrate` in test mode validates migrations work without touching production data
- When deploying, the same migration files are applied to MSSQL

## Aider (LLM Coding Agent)

```powershell
aider --model openai/qwen35B-code --openai-api-base http://127.0.0.1:11437/v1
```

- Uses llama.cpp on port 11437 (NOT Ollama)
- Model name: `qwen35B-code`
- In aider, end a command with `/exit`

## Aider Configuration

**`.env` file** (in project root):
```
OPENAI_API_KEY=llamacpp
OPENAI_BASE_URL=http://127.0.0.1:11437/v1
```

**.aider.conf.yml**:
```yaml
models:
  - openai/qwen35B-code
openai-api-key: llamacpp
openai-api-base: http://127.0.0.1:11437/v1
```

## Terminal Setup

Use **PowerShell 7** (`pwsh`) — not bash. In Zed, configure:

```json
"terminal": {
  "default_profile": "powershell",
  "detect_venv": "off",
  "profiles": {
    "powershell": {
      "command": "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
      "args": ["-NoLogo"]
    }
  }
}
```

PowerShell profile (`C:\Users\tdwdev\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`):
```powershell
# Force-load the global AI environment
$env:VIRTUAL_ENV = "C:\AI-DevTeam\ai-env"
$env:PATH = "C:\AI-DevTeam\ai-env\Scripts;" + $env:PATH

# Explicitly set the prompt to show the environment
function global:prompt {
    "$(if ($env:VIRTUAL_ENV) { '(ai-env) ' } else { '' })PS $($executionContext.SessionState.Path.CurrentLocation)$('>' * ($nestedPromptLevel + 1)) "
}
```

## Branch Strategy

| Branch | Purpose |
|--------|---------|
| `dev` | Active development |
| `main` | Production |
| `feature/phase*-...` | Feature branches |

## Architecture Notes

- **URLs:** `core.urls` included at root — routes are `/login/`, `/dashboard/`, etc. (NOT `/core/login/`)
- **yfinance 1.3.0 bug:** `fast_info.last_price` crashes. Use `_get_current_price()` helper in `core/utils/prices.py`
- **Django ORM:** pyright doesn't understand Django's runtime metaclass magic. Use `typeCheckingMode: "basic"` in pyright config
- **Known linter false positives:** `logging.handlers` import, `utils_fetch_last_prices` re-export — both have `noqa: F401`

## Phase Status

See `docs/history/specs/PhaseD/PHASED_IMPLEMENTATION_PLAN.md` for the current implementation plan.

## Memory & Skills

- **Project AGENTS.md** (`AGENTS.md`) — project-specific instructions read every session
- **Global AGENTS.md** — mirrored in `global_agents.md`; copy to `C:\Users\tdwdev\.agents\AGENTS.md` to activate
- **Global Skills** — live in `~/.agents/skills/`; I can edit them directly
- **Skills used:** `web-app-debug` — general web app debugging checklist
