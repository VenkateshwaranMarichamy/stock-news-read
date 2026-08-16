# StoxScoop — Project Handbook

A quick reference for running, configuring, and using the MoneyControl stock news scraper and classification pipeline.

---

## What This Project Does

1. **Scrapes** MoneyControl "Stocks to Watch" articles and extracts stock entries per section
2. **Stores** raw article data in PostgreSQL (`news_staging` table)
3. **Classifies** each stock entry using an LLM (Gemini) into structured events (contracts, deals, financials, etc.)
4. **Writes** classified events to the database for downstream use

---

## Prerequisites

- Python 3.11+
- PostgreSQL running and accessible
- Virtual environment set up (`.venv/`)
- `.env` file configured (see below)

---

## Setup

### 1. Activate the virtual environment

```bash
cd /Users/ambikavenkat/Documents/stox_workspace/stock-news-read
source .venv/bin/activate
```

### 2. Install dependencies (first time only)

```bash
pip install -e ".[dev]"
```

### 3. Configure `.env`

All secrets live in `.env` — never commit this file.

```dotenv
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=stk_fund
DB_USER=postgres
DB_PASSWORD=your_password
DB_SCHEMA=stoxscoop_dev

# Pool sizing (optional)
DB_MIN_POOL_SIZE=2
DB_MAX_POOL_SIZE=10

# Legacy single-provider fallback (used if llm_providers is NOT set in config.yaml)
LLM_MODEL=gemini-2.5-flash-lite
LLM_API_KEY=your_key
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# Multi-provider fallback keys (used when llm_providers IS set in config.yaml)
GEMINI_API_KEY=your_key_1          # free tier — used first
GEMINI_API_KEY_2=your_key_2        # free tier — second fallback
GEMINI_API_KEY_3=your_key_3        # free tier — third fallback
GEMINI_API_KEY_PAID=your_key_4     # paid/billing — last resort
```

### 4. Configure `config.yaml`

Key settings to know:

| Setting | Values | Description |
|---|---|---|
| `output_mode` | `file`, `database`, `both` | Where to write scraped data |
| `urls` | list of URLs | Default URLs to scrape |
| `sections` | list of section titles | Which sections to keep (empty = all) |
| `delay` | float (seconds) | Wait between HTTP requests |
| `llm_rotation_strategy` | `priority`, `round-robin` | How to cycle through LLM providers |
| `llm_providers` | list | Your 4 Gemini API keys for fallback |

The `llm_providers` block is already active with your 4 keys using `priority` strategy (key 1 first, key 4 last).

---

## Running the API Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8005
```

The API will be available at `http://localhost:8005`

Interactive docs (Swagger UI): `http://localhost:8005/docs`

---

## API Endpoints

### `GET /health`

Checks if the server and database are running.

```bash
curl http://localhost:8005/health
```

**Response:**
```json
{"status": "ok"}
```
Returns `503` with `{"status": "degraded"}` if the database is unreachable.

---

### `POST /scrape`

Scrapes one or more MoneyControl article URLs, stores them in the database, and automatically triggers LLM classification in the background.

**Request body:**
```json
{
  "urls": [
    "https://www.moneycontrol.com/news/business/markets/stocks-in-news-..."
  ]
}
```

- Accepts 1–50 URLs per request
- URLs must be valid HTTP/HTTPS

**Response:**
```json
{
  "processed": 1,
  "inserted": 1,
  "skipped": 0,
  "failures": [],
  "events_queued": 12
}
```

| Field | Description |
|---|---|
| `processed` | Number of URLs attempted |
| `inserted` | New articles added to DB |
| `skipped` | Duplicate articles (already in DB) |
| `failures` | URLs that failed to fetch/parse |
| `events_queued` | Stock entries sent to classification pipeline |

**Example:**
```bash
curl -X POST http://localhost:8005/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "urls": ["https://www.moneycontrol.com/news/business/markets/stocks-in-news-..."]
  }'
```

---

### `POST /classify`

Manually re-run the classification pipeline for specific records by their database ID. Useful for retrying failed classifications.

**Request body:**
```json
{
  "ids": [42, 43, 44]
}
```

- Accepts 1–50 IDs per request
- IDs must be positive integers
- Duplicate IDs are deduplicated automatically

**Response:**
```json
{
  "ids_processed": [42, 43],
  "ids_missing": [44],
  "ids_skipped": [],
  "total_stocks": 25,
  "events_inserted": 23,
  "events_failed": 2,
  "unresolved_stocks": 1
}
```

| Field | Description |
|---|---|
| `ids_processed` | IDs successfully classified |
| `ids_missing` | IDs not found in the database |
| `ids_skipped` | IDs with no content or pipeline errors |
| `total_stocks` | Total stock entries processed |
| `events_inserted` | Events written to database |
| `events_failed` | Events that failed to write |
| `unresolved_stocks` | Stock names not matched to a ticker |

**Example:**
```bash
curl -X POST http://localhost:8005/classify \
  -H "Content-Type: application/json" \
  -d '{"ids": [42, 43]}'
```

---

### `GET /news`

List all news records with optional filters and pagination.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `date` | `YYYY-MM-DD` | Filter by published date |
| `stock` | string | Filter by stock name |
| `source` | string | Filter by source |
| `page` | int (≥1) | Page number (default: 1) |
| `page_size` | int (1–100) | Results per page (default: 20) |

**Example:**
```bash
# All records for a date
curl "http://localhost:8005/news?date=2026-05-31&page=1&page_size=10"

# Filter by stock
curl "http://localhost:8005/news?stock=Infosys"
```

**Response:**
```json
{
  "items": [...],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```

---

### `GET /news/{id}`

Fetch a single news record by its database ID.

```bash
curl http://localhost:8005/news/42
```

Returns `404` if the ID doesn't exist.

---

### `GET /news/by-url`

Fetch a single news record by its exact article URL.

```bash
curl "http://localhost:8005/news/by-url?url=https://www.moneycontrol.com/news/..."
```

Returns `404` if the URL hasn't been scraped yet.

---

## Running the Scraper from the Command Line

You can also run the scraper directly without starting the API server.

```bash
# Use URLs from config.yaml
python run.py

# Pass a specific URL
python run.py "https://www.moneycontrol.com/news/..."

# Pass multiple URLs
python run.py "https://..." "https://..."

# Read URLs from a text file (one per line)
python run.py --file urls.txt

# Save output to a specific JSON file
python run.py --output my_results.json
```

Output mode is controlled by `output_mode` in `config.yaml`:
- `file` — writes JSON to `output/` folder only
- `database` — writes to PostgreSQL only (no JSON file)
- `both` — writes to both

---

## How the LLM Fallback Works

With 4 keys configured and `priority` strategy:

```
Request comes in
  → Try gemini-key1 (free tier)
      ✓ Success → done
      ✗ Quota hit → mark exhausted, try next
  → Try gemini-key2 (free tier)
      ✓ Success → done
      ✗ Quota hit → mark exhausted, try next
  → Try gemini-key3 (free tier)
      ✓ Success → done
      ✗ Quota hit → mark exhausted, try next
  → Try gemini-paid (billing key, no daily cap)
      ✓ Success → done
      ✗ All failed → classification result = "failed"
```

- Keys reset to `available` automatically when their rate-limit timer expires
- `quota_exhausted` keys stay parked until the application restarts (or you call a reset)
- All fallback activity is logged as WARNING level

---

## Running Tests

```bash
# All tests
.venv/bin/python -m pytest

# Just the pipeline tests
.venv/bin/python -m pytest tests/pipeline/ -v

# With output
.venv/bin/python -m pytest -s
```

---

## Project Structure

```
stock-news-read/
├── app/
│   ├── main.py              # FastAPI app, startup/shutdown
│   ├── schemas.py           # Request/response models
│   ├── routers/
│   │   ├── health.py        # GET /health
│   │   ├── scrape.py        # POST /scrape
│   │   ├── classify.py      # POST /classify
│   │   └── news.py          # GET /news, /news/{id}, /news/by-url
│   ├── pipeline/
│   │   ├── provider_manager.py  # LLM multi-key fallback manager
│   │   ├── classifier.py        # LLM event classifier
│   │   ├── resolver.py          # Stock name → ticker resolver
│   │   ├── event_writer.py      # Write events to DB
│   │   └── orchestrator.py      # Pipeline coordinator
│   └── db/
│       ├── pool.py          # DB connection pool
│       ├── writer.py        # Write scraped articles to DB
│       └── queries.py       # Read queries
├── moneycontrol_scraper/    # Scraper (HTTP fetch + HTML parse)
├── tests/                   # Test suite
├── config.yaml              # Main configuration
├── .env                     # Secrets (never commit)
├── run.py                   # CLI entry point
└── HANDBOOK.md              # This file
```

---

## Common Issues

**Server won't start / DB connection refused**
- Check `DB_HOST`, `DB_PORT`, `DB_PASSWORD` in `.env`
- Make sure PostgreSQL is running: `pg_isready -h localhost -p 5432`

**Classification returns `status: "failed"` for all entries**
- Check your Gemini API keys in `.env` are correct and not expired
- Check logs for `quota_exhausted` warnings — all 4 keys may have hit their limit for the day
- Confirm `llm_providers` block is active (not commented out) in `config.yaml`

**`events_queued: 0` after scraping**
- `output_mode` in `config.yaml` might be set to `file` — change to `database` or `both`
- Database pool may have failed to initialize — check startup logs

**Duplicate article not re-classified**
- Use `POST /classify` with the existing record's ID to force re-classification
- The `/scrape` endpoint skips articles already in the database but still runs the pipeline if classification is incomplete
