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
4. **`api_key='not-used'`**: llama.cpp ignored the key. Live test confirms
   Halogen needs no API key, so add `VL_MODEL_API_KEY` with default `'not-used'`
   and it can stay unset in `.env`.
5. **Image format**: base64 PNG data URLs with `"detail": "high"` — standard
   OpenAI chat-completions vision format. Live test confirms Halogen accepts
   `detail: "high"`; keep as-is.
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
- Pass `api_key=settings.VL_MODEL_API_KEY`, `max_tokens=settings.VL_MAX_TOKENS`, `temperature=settings.VL_TEMPERATURE` to `VLClient(...)` — plain `settings.X` for the new settings too, no `getattr` fallbacks (settings always defines them).
- Module docstrings: drop "Qwen2.5-VL-7B on port 11440" wording.

### `.env.example`
Add a VL section:
```
# VL (vision) model for PDF statement parsing — OpenAI-compatible endpoint.
# All lines optional: unset means the code default (Halogen on saturn Ubuntu)
# is used. Uncomment and change only to override.
# VL_MODEL_BASE_URL=http://127.0.0.1:11435/v1
# VL_MODEL_NAME=halogen-qwen3.8-flash-next
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
Decision: correct the doc only; do not add validation.

## Migrations

None. No models touched.

## Tests

All existing VL tests already mock `VLClient` (`tests/test_vl_parsers.py` patches
the class); none may open a network connection. `VLClient.__init__` runs
`_diag_connection`, so **any test that constructs a real VLClient would hit the
network — none may; keep patching the class**.

Add (`tests/test_vl_config.py`, new):
1. `test_parsers_use_settings_model_name` — patch `VLClient` in each of the three
   modules, run the entry point with a stubbed render + canned JSON (monthly
   already has this shape), and assert `VLClient` was constructed with
   `model=settings.VL_MODEL_NAME` and `base_url=settings.VL_MODEL_BASE_URL`.
2. `test_diag_connection_tolerates_unknown_shape` — call
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
   `_diag_connection` for both `/models` shapes; docstrings. Log a warning when
   the response's `finish_reason` is `'length'`, so truncated output is visible
   (reasoning tokens count toward `max_tokens` — see Live test results).
   Unit tests for `_diag_connection` (test 2 above). Commit.
3. **Call sites**: `parsers_monthly_vl.py`, `parsers_annual_vl.py`,
   `classify_pdf_vl.py` — plain `settings.VL_MODEL_NAME`, pass new kwargs,
   de-llama.cpp docstrings. Add test 1 (constructor receives settings values).
   Commit.
4. **Docs/config**: `.env.example`, `AGENTS.md` VL section, `docs/deployment.md`.
   Commit.
5. **Verification (manual, tdw only — Pi has no real PDFs)**: with Halogen up on
   11435, tdw runs one real monthly PDF through `parse_rbc_monthly_vl` (or
   `manage_inbox_import` on a copy) and confirms: connection log OK, JSON parses,
   transactions land. Adjust `VL_MAX_TOKENS`/`VL_TEMPERATURE` via `.env` if the
   model truncates (watch for the new `finish_reason='length'` warning).
   Not a code step; tdw records findings in the PR/commit summary.

## Live test results

tdw ran a live test against Halogen on 11435 (made-up image, same call format as
`VLClient`):

- **No API key needed** — default `not-used` is fine; `VL_MODEL_API_KEY` stays optional.
- **Model id confirmed**: `halogen-qwen3.8-flash-next`.
- **`detail: "high"` accepted.**
- **`finish_reason: stop` after ~7 s.**
- **Thinking output is not a problem**: Halogen returns its reasoning in a
  separate `reasoning` field and `message.content` is clean JSON — no new strip
  rule needed in `_extract_json_from_text`.
- **Reasoning tokens count toward `max_tokens`** (334 of 468 completion tokens
  were reasoning in the test). Real pages with long JSON may need a higher
  `VL_MAX_TOKENS`; step 2 adds a `finish_reason='length'` warning so truncation
  is visible.

## Open questions

1. **Max tokens cap** — is 8192 within Halogen's limit for the annual-report
   prompt (multi-page, long JSON), and is it enough once reasoning tokens are
   counted? Settle during tdw's step 5 real-PDF check; tune via `.env`.

Resolved by tdw's review: `VL_MAX_PAGES` is left alone (item 8 above);
`docs/deployment.md` is corrected by doc change only, no validation added.
