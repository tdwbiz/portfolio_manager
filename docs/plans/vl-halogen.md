# Plan: make the VL model configurable and point it at Halogen

## Goal

The vision-language model that reads RBC PDF statements is hard-coded to the old
llama.cpp set-up (`http://127.0.0.1:11440/v1`, model `qwen2.5-vl`). Port 11440 is
no longer a VL model, so PDF VL imports are broken. Make `VL_MODEL_BASE_URL` and
`VL_MODEL_NAME` come from `.env` — exactly the way `RBC_ACCOUNTS` is read — with
new defaults pointing at **Halogen** (OpenAI-compatible, accepts images):

- `VL_MODEL_BASE_URL` default: `http://127.0.0.1:11435/v1`
- `VL_MODEL_NAME` default: `halogen-qwen3.8-flash-next`

No behaviour change otherwise: same prompts, same JSON coercion, same regex
fallback, same call sites.

## Background — who uses the VL settings

| File | Use |
|---|---|
| `portfolio_manager/settings.py` (~L255–264) | Defines `VL_MODEL_BASE_URL`, `VL_MODEL_NAME`, `VL_PAGE_DPI`, `VL_MAX_RETRIES`, `VL_TIMEOUT_SECONDS`, `VL_MAX_PAGES`, `VL_MONTHLY_MAX_PAGES` |
| `portfolio_manager/settings_test.py` L32 | Hard-codes `VL_MODEL_BASE_URL = "http://127.0.0.1:11440/v1"` |
| `imports/llm_client.py` | `VLClient` — the only code that talks to the model (OpenAI SDK over httpx). Docstrings say "qwen2.5-vl … llama.cpp server (port 11440)" |
| `imports/parsers_monthly_vl.py` L375–379 | `parse_rbc_monthly_vl` reads settings; `getattr(settings, 'VL_MODEL_NAME', 'gemma-gen')` |
| `imports/parsers_annual_vl.py` L168–172 | Same pattern; fallback string `'qwen2.5-vl-7b-instruct-q8_0'` |
| `imports/classify_pdf_vl.py` L123–127 | Same pattern; fallback string `'qwen2.5-vl-7b-instruct-q8_0'` |
| `imports/views.py`, `core/management/commands/manage_inbox_import.py` | Call the parsers above; never touch `VL_MODEL_*` directly — no changes needed |
| `tests/test_vl_parsers.py` | Already mocks `VLClient` (patch class) — never calls the model. Keep it that way |

### qwen2.5-vl / llama.cpp specifics that may not fit Halogen

Found while reading the code:

1. **Three different hard-coded model-name fallbacks**: settings default
   `qwen2.5-vl`, `parsers_monthly_vl` fallback `gemma-gen`, annual/classify
   fallbacks `qwen2.5-vl-7b-instruct-q8_0`. All should collapse to the single
   settings value (the `getattr` fallbacks are dead weight since settings
   always defines the key — replace with plain `settings.VL_MODEL_NAME`).
2. **`VLClient._diag_connection`** (`llm_client.py` ~L176) GETs `{base_url}/models`
   and reads `resp.json().get('models', [])` with `m['name']`. The OpenAI
   `/v1/models` schema returns `{"data": [{"id": ...}]}`; llama.cpp happened to
   also return `models`/`name`. Halogen's shape must be checked; at minimum make
   the diagnostic tolerant (wrap in try, log raw ids from either shape). It is
   only a log line — it must never raise.
3. **Generation params** in `VLClient.extract_json`: `temperature=0.1`,
   `max_tokens=8192`. Halogen (Flash-Next) may cap `max_tokens` differently or
   use a different sampling default. Keep the values but make them settings-
   driven (`VL_MAX_TOKENS`, `VL_TEMPERATURE`) so they can be tuned via `.env`
   without a code change.
4. **`api_key='not-used'`**: llama.cpp ignored the key. If Halogen requires an
   auth token, add `VL_MODEL_API_KEY` (default `'not-used'`). See open questions.
5. **Image format**: base64 PNG data URLs with `"detail": "high"` — standard
   OpenAI chat-completions vision format; Halogen is OpenAI-compatible so this
   should work. `detail` is an OpenAI extension; harmless if ignored, but note
   it as a thing to verify on the first live smoke test.
6. **Response parsing** (`_extract_json_from_text`): strips markdown fences /
   first `{...}` block, fixes trailing commas. Model-agnostic; keep as-is.
7. **No mmproj handling in this repo** — `--mmproj` was a llama.cpp launch flag,
   nothing to change in code. Only stale *mentions* of 11440/qwen2.5-vl/llama.cpp
   in docstrings and docs.
8. **`VL_MAX_PAGES`** (settings L261) is defined but unused anywhere — leave it
   (out of scope) or note it; do not remove in this feature.

## What changes

### `portfolio_manager/settings.py`
```python
# Vision-language (VL) model settings (via .env, like RBC_ACCOUNTS)
VL_MODEL_BASE_URL = os.getenv("VL_MODEL_BASE_URL", "http://127.0.0.1:11435/v1").strip()
VL_MODEL_NAME = os.getenv("VL_MODEL_NAME", "halogen-qwen3.8-flash-next").strip()
VL_MODEL_API_KEY = os.getenv("VL_MODEL_API_KEY", "not-used").strip() or "not-used"
VL_PAGE_DPI = int(os.getenv("VL_PAGE_DPI", "150"))
VL_MAX_RETRIES = int(os.getenv("VL_MAX_RETRIES", "2"))
VL_TIMEOUT_SECONDS = int(os.getenv("VL_TIMEOUT_SECONDS", "600"))
VL_MAX_PAGES = int(os.getenv("VL_MAX_PAGES", "3"))
VL_MONTHLY_MAX_PAGES = int(os.getenv("VL_MONTHLY_MAX_PAGES", "0"))
VL_MAX_TOKENS = int(os.getenv("VL_MAX_TOKENS", "8192"))
VL_TEMPERATURE = float(os.getenv("VL_TEMPERATURE", "0.1"))
```
Update the section comment (drop "qwen2.5-vl-72b on port 11440 via llama.cpp").

### `portfolio_manager/settings_test.py`
Replace the hard-coded 11440 line with the Halogen defaults, so tests exercise
the same values production will use by default:
```python
VL_MODEL_BASE_URL = "http://127.0.0.1:11435/v1"
VL_MODEL_NAME = "halogen-qwen3.8-flash-next"
```
(Tests still must never open a socket — see Tests below.)

### `imports/llm_client.py`
- `VLClient.__init__(base_url, model, api_key='not-used', timeout=120, max_retries=2, max_tokens=8192, temperature=0.1)`; store `max_tokens`/`temperature`; pass `api_key` to `OpenAI(...)`.
- `extract_json` uses `self._max_tokens` / `self._temperature` instead of literals.
- `_diag_connection`: tolerant of both `{"models":[{"name":…}]}` (llama.cpp) and `{"data":[{"id":…}]}` (OpenAI/Halogen) response shapes; never raises (already wrapped in try/except — keep and broaden).
- Docstrings: replace "qwen2.5-vl … port 11440 / llama.cpp" with "any OpenAI-compatible vision model (default: Halogen)".

### `imports/parsers_monthly_vl.py`, `imports/parsers_annual_vl.py`, `imports/classify_pdf_vl.py`
- `model = getattr(settings, 'VL_MODEL_NAME', '<stale literal>')` → `model = settings.VL_MODEL_NAME`.
- Pass `api_key=settings.VL_MODEL_API_KEY`, `max_tokens=settings.VL_MAX_TOKENS`, `temperature=settings.VL_TEMPERATURE` to `VLClient(...)` (use `getattr(settings, ..., default)` for the two new ones so the parsers don't break on settings that lack them).
- Module docstrings: drop "Qwen2.5-VL-7B on port 11440" wording.

### `.env.example`
Add a VL section:
```
# VL (vision) model for PDF statement parsing — OpenAI-compatible endpoint.
# Default points at Halogen on saturn Ubuntu; leave unset to use the default.
VL_MODEL_BASE_URL=http://127.0.0.1:11435/v1
VL_MODEL_NAME=halogen-qwen3.8-flash-next
# VL_MODEL_API_KEY=not-used
# VL_MAX_TOKENS=8192
# VL_TEMPERATURE=0.1
```

### `AGENTS.md` (VL section, "RBC PDF statements" → "VL model (PDF reading)")
Rewrite the bullet: VL model is now env-driven (`VL_MODEL_BASE_URL`,
`VL_MODEL_NAME` in `.env`, defaults Halogen on port 11435, model
`halogen-qwen3.8-flash-next`); remove "not running at the moment" and the
"Planned" line; keep the caveat that CSV Activity remains the master for
transactions and VL output is still heavily post-processed / regex-fallback
exists.

### `docs/deployment.md`
Update the two stale spots: example `VL_MODEL_BASE_URL=http://localhost:11440/v1`
→ Halogen example, and the troubleshooting note claiming the app raises
`ImproperlyConfigured` without it (it never has — it falls back to the default).

## Migrations

None. No models touched.

## Tests

All existing VL tests already mock `VLClient` (`tests/test_vl_parsers.py` patches
the class); none may open a network connection. `VLClient.__init__` runs
`_diag_connection`, so **any test that constructs a real VLClient would hit the
network — none may; keep patching the class**.

Add (`tests/test_vl_config.py`, new):
1. `test_settings_defaults_are_halogen` — with `VL_MODEL_BASE_URL`/`VL_MODEL_NAME`
   unset (use `override_env`-style `patch.dict(os.environ, …)` + re-import, or
   simply assert the values `settings_test.py` provides are the Halogen ones).
2. `test_env_overrides` — `importlib.reload` of settings is fragile in Django;
   instead test the env-parsing pattern by calling the same `os.getenv(...)`
   defaults helper if one is extracted, or drop this test and rely on (1).
   Decide during implementation; prefer the simple assertion.
3. `test_parsers_use_settings_model_name` — patch `VLClient` in each of the three
   modules, run the entry point with a stubbed render + canned JSON (monthly
   already has this shape), and assert `VLClient` was constructed with
   `model=settings.VL_MODEL_NAME` and `base_url=settings.VL_MODEL_BASE_URL`.
4. `test_diag_connection_tolerates_unknown_shape` — call
   `VLClient._diag_connection` with a patched `httpx.Client` returning
   `{"data": [{"id": "x"}]}`, `{"models": [{"name": "x"}]}` and garbage; assert
   no exception.

Change: none of the existing tests should need edits beyond the settings_test
value change (they don't assert on 11440).

## Steps

1. **Settings**: new `VL_MODEL_*` block in `settings.py` (new defaults +
   `VL_MODEL_API_KEY`, `VL_MAX_TOKENS`, `VL_TEMPERATURE`); update
   `settings_test.py`. Run full suite. Commit.
2. **VLClient**: `api_key`, `max_tokens`, `temperature` parameters; robust
   `_diag_connection` for both `/models` shapes; docstrings. Unit tests for
   `_diag_connection` (test 4 above). Commit.
3. **Call sites**: `parsers_monthly_vl.py`, `parsers_annual_vl.py`,
   `classify_pdf_vl.py` — plain `settings.VL_MODEL_NAME`, pass new kwargs,
   de-llama.cpp docstrings. Add test 3 (constructor receives settings values).
   Commit.
4. **Docs/config**: `.env.example`, `AGENTS.md` VL section, `docs/deployment.md`.
   Commit.
5. **Verification (manual, tdw or Pi with Halogen running)**: with Halogen up on
   11435, run one real monthly PDF through `parse_rbc_monthly_vl` (or
   `manage_inbox_import` on a copy) and confirm: connection log OK, JSON parses,
   transactions land. Adjust `VL_MAX_TOKENS`/`VL_TEMPERATURE` via `.env` if the
   model truncates. Not a code step; record findings in the PR/commit summary.

## Open questions

1. **Does Halogen require an API key?** Plan assumes no (default `not-used`).
   If it does, tdw puts `VL_MODEL_API_KEY` in `.env` — no code change needed,
   but `.env.example` comment should stop saying it's optional.
2. **Exact Halogen model id** — `halogen-qwen3.8-flash-next` per the task;
   confirm against Halogen's `/v1/models` output during step 5 and fix the
   default if the id differs.
3. **Max tokens cap** — is 8192 within Halogen's limit for the annual-report
   prompt (multi-page, long JSON)? If Halogen caps lower, lower the default.
4. **Does Halogen honour `detail: "high"`?** Harmless if ignored; if it errors,
   drop the `detail` key (small follow-up).
5. **Thinking/reasoning output**: some Flash-class models emit chain-of-thought
   or `<think>` blocks before the JSON. `_extract_json_from_text` survives
   fences and stray prose, but if Halogen wraps output differently we may need
   one more strip rule — check in step 5.
6. **`VL_MAX_PAGES`** is defined but unused — delete it in this feature or leave
   it? Plan currently leaves it alone.
7. Should `docs/deployment.md`'s `ImproperlyConfigured` claim be fixed by adding
   the validation (old review suggestion) or just by correcting the doc? Plan
   corrects the doc only.
