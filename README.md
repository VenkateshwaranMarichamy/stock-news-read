# MoneyControl Stocks News Scraper

A Python CLI tool that fetches MoneyControl **"Stocks to Watch"** morning articles and extracts structured stock news into JSON — organised by section (e.g. *Stocks to Watch*, *Bulk Deals*) and stock name.

---

## Output format

Each scraped article produces one record:

```json
{
  "date": "2024-05-21",
  "url": "https://www.moneycontrol.com/news/...",
  "sections": {
    "Stocks to Watch": {
      "Reliance Industries": "RISE Worldwide announced a partnership with Major League Baseball...",
      "HG Infra Engineering": "Considering the uncertainty regarding the execution of projects..."
    },
    "Bulk Deals": {
      "Jaro Institute of Technology": "Singularity Growth Opportunities Fund II exited..."
    }
  }
}
```

Multiple articles are collected into a JSON array. Output files are saved automatically to the `output/` folder with a timestamp filename (`YYYY-MM-DD_HH-MM-SS.json`).

---

## Project structure

```
stock-news-read/
├── config.yaml                  ← URLs, section filter, output folder, delay
├── pyproject.toml               ← dependencies
├── output/                      ← auto-created; timestamped JSON files saved here
├── moneycontrol_scraper/
│   ├── cli.py                   ← entry point (main)
│   ├── config.py                ← config loader (reads config.yaml)
│   ├── http_client.py           ← HTTP fetching with User-Agent + timeout
│   ├── parser.py                ← HTML → sections/stocks/date extraction
│   ├── serialiser.py            ← JSON serialisation + UTF-8 sanitisation
│   ├── url_reader.py            ← URL collection from CLI args / file
│   ├── models.py                ← OutputRecord dataclass
│   └── exceptions.py            ← ScraperError hierarchy
└── tests/                       ← 109 unit + property-based tests
```

---

## Setup

### Requirements

- Python 3.11 or later
- pip

### 1. Create a virtual environment

```bash
cd /Users/ambikavenkat/Documents/stox_workspace/stock-news-read
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### 2. Install dependencies

```bash
pip install requests beautifulsoup4 pyyaml
# For running tests also install:
pip install pytest hypothesis
```

---

## Configuration (`config.yaml`)

Edit `config.yaml` in the project root to control the scraper without touching code:

```yaml
# URLs to scrape when none are passed on the command line
urls:
  - "https://www.moneycontrol.com/news/business/markets/stocks-to-watch-today-..."
  - "https://www.moneycontrol.com/news/business/markets/stocks-to-watch-today-..."

# Only keep these sections in the output (case-insensitive partial match).
# Leave empty to include ALL sections found on the page.
sections:
  - "Stocks to Watch"
  - "Bulk Deals"
  - "Bulk and Block Deals"
  - "Block Deals"

# Folder where timestamped JSON files are saved automatically
output_dir: "output"

# Seconds to wait between consecutive HTTP requests (avoids rate-limiting)
delay: 1.0
```

**Key points:**
- Add the daily MoneyControl URL to `urls` each morning, or pass it directly on the command line.
- The `sections` list filters which sections appear in the JSON. Remove entries to include more sections, or leave it empty (`sections: []`) to keep everything.
- Output files are named `output/YYYY-MM-DD_HH-MM-SS.json` automatically.

---

## Running the scraper

All commands assume you are in the project root with the venv active.

### Use URLs from `config.yaml` (default)

```bash
PYTHONPATH=. python -m moneycontrol_scraper.cli
```

### Scrape a specific URL (overrides config URLs)

```bash
PYTHONPATH=. python -m moneycontrol_scraper.cli \
  "https://www.moneycontrol.com/news/business/markets/stocks-to-watch-today-apollo-hospitals-jubilant-foodworks-hg-infra-pace-digitek-gpt-infra-metro-brands-teamlease-sammaan-capital-in-focus-on-21-may-13925662.html"
```

### Scrape multiple URLs at once

```bash
PYTHONPATH=. python -m moneycontrol_scraper.cli \
  "https://www.moneycontrol.com/news/..." \
  "https://www.moneycontrol.com/news/..."
```

### Scrape from a URL list file

Create a plain-text file (e.g. `urls.txt`) with one URL per line. Lines starting with `#` and blank lines are ignored:

```
# May 2024 articles
https://www.moneycontrol.com/news/business/markets/stocks-to-watch-today-...
https://www.moneycontrol.com/news/business/markets/stocks-to-watch-today-...
```

Then run:

```bash
PYTHONPATH=. python -m moneycontrol_scraper.cli --file urls.txt
```

### Save to a specific file

```bash
PYTHONPATH=. python -m moneycontrol_scraper.cli \
  "https://www.moneycontrol.com/news/..." \
  --output my_results.json
```

### Use a custom config file

```bash
PYTHONPATH=. python -m moneycontrol_scraper.cli --config /path/to/my_config.yaml
```

### All options

```
usage: moneycontrol-scraper [-h] [--file FILE] [--config CONFIG]
                             [--output OUTPUT] [--delay SECONDS]
                             [URL ...]

positional arguments:
  URL               One or more MoneyControl article URLs. Overrides config.yaml URLs.

options:
  -h, --help        show this help message and exit
  --file, -f FILE   Plain-text file with one URL per line
  --config, -c CONFIG
                    Path to YAML config file (default: config.yaml)
  --output, -o OUTPUT
                    Output file path (default: output/YYYY-MM-DD_HH-MM-SS.json)
  --delay, -d SECONDS
                    Delay between requests in seconds (default: from config.yaml)
```

---

## Where to find the output

By default every run saves a file to the `output/` folder:

```
output/
├── 2024-05-21_09-30-00.json
├── 2024-05-20_08-15-42.json
└── ...
```

Each file is a JSON array. Open it in any text editor, or load it in Python:

```python
import json

with open("output/2024-05-21_09-30-00.json") as f:
    articles = json.load(f)

for article in articles:
    print(article["date"], article["url"])
    for section, stocks in article["sections"].items():
        print(f"  [{section}]")
        for stock, news in stocks.items():
            print(f"    {stock}: {news[:80]}...")
```

---

## Running the tests

```bash
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

The suite has **109 tests** covering:
- HTTP client (property-based + unit)
- HTML parser — sections, stocks, dates (property-based + unit)
- Serialiser — round-trip, UTF-8 sanitisation (property-based + unit)
- URL reader (property-based + unit)
- CLI integration (property-based + unit)

---

## How it works (implementation overview)

1. **`config.py`** — loads `config.yaml` using PyYAML into a `ScraperConfig` dataclass. Provides `filter_sections()` to apply the section whitelist.

2. **`http_client.py`** — `HTTPClient.fetch(url)` makes a GET request with a browser User-Agent header and 30-second timeout. Raises `ScraperFetchError` on non-200 responses or network failures.

3. **`parser.py`** — `ArticleParser.parse(html, url)` uses BeautifulSoup4 to:
   - Locate the article body (`div.arti-flow` or fallback selectors)
   - Strip widget/sidebar noise elements
   - Walk block elements as a state machine: section headings → stock headings → paragraphs
   - Extract the publication date from JSON-LD → meta tags → `<time>` → URL regex → `null`

4. **`serialiser.py`** — `serialise(records)` sanitises all strings (replaces Unicode surrogates and null bytes) then encodes to indented JSON. `write_output(json_str, path)` writes to a file or stdout.

5. **`url_reader.py`** — `read_urls(args, file_path)` merges CLI positional args and `--file` lines, skipping blank lines and `#` comments.

6. **`cli.py`** — `main()` wires everything together: load config → collect URLs → fetch/parse loop with per-URL error recovery → filter sections → serialise → write to timestamped output file.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'yaml'` | `pip install pyyaml` |
| `ModuleNotFoundError: No module named 'bs4'` | `pip install beautifulsoup4` |
| Empty `sections: {}` in output | The page structure may have changed. Check the URL is a valid "Stocks to Watch" article. |
| `ScraperFetchError: URL ... returned status 403` | MoneyControl may be rate-limiting. Increase `delay` in `config.yaml`. |
| Output file not created | Check the `output_dir` path in `config.yaml` is writable. |
