# Vera Engagement Bot

A deterministic FastAPI implementation for the magicpin AI Challenge. The bot composes merchant-facing and customer-facing WhatsApp messages from four contexts:

`category + merchant + trigger + optional customer`

The implementation is rules-first so every message is grounded in the supplied dataset and can fall back safely without an LLM.

## Setup

Requires Python 3.10+.

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the provider key you want to use. The application loads `.env` automatically. The `.env` file is ignored by Git.

```powershell
Copy-Item .env.example .env
```

Start the service:

```cmd
.venv\Scripts\python.exe -m uvicorn bot:app --host 0.0.0.0 --port 8080
```

Useful local URLs:

- `http://localhost:8080/docs` - Swagger UI
- `http://localhost:8080/v1/healthz` - liveness and context counts
- `http://localhost:8080/v1/metadata` - bot identity

No API key is required to run the bot when `BOT_LLM_PROVIDER=none`.

The bot can optionally use an LLM for wording. It remains deterministic by default. To enable Gemini-assisted wording in the bot process:

```powershell
$env:BOT_LLM_PROVIDER = "gemini"
$env:GEMINI_API_KEY = "your-new-gemini-key"
$env:BOT_LLM_MODEL = "gemini-flash-latest"
```

The LLM receives only the pushed contexts and must return JSON. Messages are rejected when they contain URLs, category taboo phrases, no recipient name, or no supplied context anchor; the deterministic composer is then used. The request timeout is 8 seconds.

The judge simulator needs a provider key for scoring. The default configuration uses Gemini. On Windows PowerShell, set the Gemini key only for the current terminal session:

```powershell
$env:GEMINI_API_KEY = "your-new-gemini-key"
$env:LLM_MODEL = "gemini-3.8-flash"
.venv\Scripts\python.exe judge_simulator.py
```

In Command Prompt:

```cmd
set GEMINI_API_KEY=your-new-gemini-key
set LLM_MODEL=gemini-3.8-flash
.venv\Scripts\python.exe judge_simulator.py
```

The simulator prints `LLM Provider: Gemini (<model>)` at startup. That printed model is the model used for judge scoring. If `LLM_MODEL` is unset, the Gemini provider default in `judge_simulator.py` is used.

Never commit API keys. If a key is exposed, revoke it immediately and create a replacement.

## Architecture

- `bot.py` - FastAPI routes and judge request routing.
- `context_store.py` - thread-safe in-memory context store with version replacement and stale-version rejection.
- `composer.py` - grounded trigger-specific composition and template parameter selection.
- `conversation.py` - opt-out, auto-reply, intent-transition, hostile/off-topic, and duplicate-reply handling.
- `validation.py` - action/reply schema checks, taboo phrase checks, duplicate detection, and safe fallbacks.
- `generate_submission.py` - creates the offline JSONL submission using the same composer as the API.

The service exposes the required endpoints:

- `GET /v1/healthz`
- `GET /v1/metadata`
- `POST /v1/context`
- `POST /v1/tick`
- `POST /v1/reply`

It also supports `POST /v1/teardown` for clearing in-memory state between test runs.

## Context and conversation behavior

`POST /v1/context` stores category, merchant, customer, and trigger payloads by `(scope, context_id)`. A newer version replaces an older version; duplicate or stale versions return `409`.

`POST /v1/tick` resolves trigger, merchant, category, and optional customer contexts. It skips missing, expired, suppressed, opted-out, or non-consented customer triggers. It returns at most 20 actions per tick.

`POST /v1/reply` handles clear action intent immediately, waits on the first canned auto-reply, ends after repeated auto-replies, ends on opt-out, and prevents ended conversations from reopening.

## Dataset and submission

Generate the expanded deterministic dataset:

```cmd
.venv\Scripts\python.exe dataset\generate_dataset.py --seed-dir dataset --out expanded
```

Generate the required 30-record submission file:

```cmd
.venv\Scripts\python.exe generate_submission.py --expanded expanded --output submission.jsonl
```

The generated records contain `test_id`, `body`, `cta`, `send_as`, `suppression_key`, and `rationale`.

## Tests

Run the automated tests:

```cmd
.venv\Scripts\python.exe -m pytest -q
```

The tests cover context versioning, all seed trigger kinds, customer consent, suppression, expiration, validation, opt-out, auto-reply, intent transition, and HTTP routing.

## Judge simulator

`judge_simulator.py` uses an external or local LLM only to score the bot. Configure its provider and key near the top of that file when scoring is needed. The bot itself remains deterministic and does not require that key.

Run the simulator while the bot is running:

```cmd
.venv\Scripts\python.exe judge_simulator.py
```

## Tradeoffs and limitations

The challenge version uses in-memory state because the judge keeps one process alive for a test run. A restart clears contexts and conversations. The composer does not call external merchant, customer, or research APIs; it uses only pushed context. An LLM could improve wording later, but it should remain behind validation with a deterministic fallback to avoid invented facts, taboo language, or malformed responses.
