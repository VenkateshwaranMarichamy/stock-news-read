"""Command-line interface for the MoneyControl scraper."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from moneycontrol_scraper.config import filter_sections, load_config
from moneycontrol_scraper.exceptions import ScraperFetchError, ScraperInputError, ScraperOutputError
from moneycontrol_scraper.http_client import HTTPClient
from moneycontrol_scraper.parser import ArticleParser
from moneycontrol_scraper.serialiser import serialise, write_output
from moneycontrol_scraper.url_reader import read_urls


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argparse parser."""
    parser = argparse.ArgumentParser(
        prog="moneycontrol-scraper",
        description=(
            "Fetch and parse MoneyControl 'Stocks to Watch' articles into "
            "structured JSON.  URLs and section filters can be set in "
            "config.yaml or passed directly on the command line."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use URLs from config.yaml (default)
  python -m moneycontrol_scraper.cli

  # Scrape a specific URL (overrides config URLs)
  python -m moneycontrol_scraper.cli https://www.moneycontrol.com/...

  # Scrape multiple URLs from a file
  python -m moneycontrol_scraper.cli --file urls.txt

  # Save to a specific file instead of the auto-timestamped output folder
  python -m moneycontrol_scraper.cli --output my_output.json

  # Use a custom config file
  python -m moneycontrol_scraper.cli --config /path/to/config.yaml
""",
    )

    parser.add_argument(
        "urls",
        nargs="*",
        metavar="URL",
        help=(
            "One or more MoneyControl article URLs to scrape. "
            "When provided, these override the URLs in config.yaml."
        ),
    )

    parser.add_argument(
        "--file", "-f",
        metavar="FILE",
        default=None,
        help=(
            "Path to a plain-text file containing one URL per line. "
            "Blank lines and lines beginning with '#' are ignored."
        ),
    )

    parser.add_argument(
        "--config", "-c",
        metavar="CONFIG",
        default=None,
        help=(
            "Path to a YAML config file (default: config.yaml in the project root). "
            "Controls URLs, section filter, output directory, and request delay."
        ),
    )

    parser.add_argument(
        "--output", "-o",
        metavar="OUTPUT",
        default=None,
        help=(
            "File path where the JSON result will be written. "
            "When omitted, output is saved to output_dir/YYYY-MM-DD_HH-MM-SS.json "
            "as configured in config.yaml."
        ),
    )

    parser.add_argument(
        "--delay", "-d",
        metavar="SECONDS",
        type=float,
        default=None,
        help=(
            "Delay in seconds between consecutive HTTP requests. "
            "Overrides the value in config.yaml (default: 1.0)."
        ),
    )

    return parser


def _resolve_output_path(cfg_output_dir: str) -> str:
    """Build a timestamped output file path inside *cfg_output_dir*.

    Creates the directory if it does not exist.

    Returns:
        Absolute path string like ``output/2024-05-21_09-30-00.json``.
    """
    out_dir = Path(cfg_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return str(out_dir / f"{timestamp}.json")


def main() -> None:
    """Entry point for the moneycontrol-scraper command.

    Execution order:
    1. Configure logging.
    2. Parse CLI arguments.
    3. Load config.yaml (or --config path).
    4. Collect URLs: CLI args / --file take priority; fall back to config URLs.
    5. Determine output path: --output flag, else auto-timestamped file in output_dir.
    6. Run the fetch-parse loop with per-URL error recovery and inter-request delay.
    7. Filter sections according to the config whitelist.
    8. Serialise and write output.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    args = build_parser().parse_args()

    # --- Load config -------------------------------------------------------
    cfg = load_config(args.config)

    # --- Collect URLs -------------------------------------------------------
    # CLI positional args / --file take priority over config.yaml URLs.
    try:
        cli_urls = read_urls(args.urls, args.file)
    except ScraperInputError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    urls = cli_urls if cli_urls else cfg.urls

    if not urls:
        build_parser().print_usage(sys.stderr)
        print(
            "\nerror: no URLs provided — pass URLs on the command line, "
            "use --file, or add them to config.yaml",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Resolve delay ------------------------------------------------------
    delay = args.delay if args.delay is not None else cfg.delay

    # --- Resolve output path ------------------------------------------------
    output_path = args.output if args.output else _resolve_output_path(cfg.output_dir)

    # --- Scrape loop --------------------------------------------------------
    http_client = HTTPClient()
    parser = ArticleParser()
    records = []

    logging.info("Scraping %d URL(s)...", len(urls))

    for index, url in enumerate(urls):
        try:
            logging.info("[%d/%d] Fetching %s", index + 1, len(urls), url)
            html = http_client.fetch(url)
            record = parser.parse(html, url)

            # Apply section filter from config
            if cfg.sections:
                record.sections = filter_sections(record.sections, cfg.sections)

            records.append(record)
            logging.info(
                "  → date=%s  sections=%s",
                record.date,
                list(record.sections.keys()),
            )
        except ScraperFetchError as exc:
            logging.error("Failed to fetch %s: %s", url, exc)
        except Exception as exc:  # noqa: BLE001
            logging.error("Failed to parse %s: %s", url, exc)

        # Sleep between requests, but not after the last URL.
        if index < len(urls) - 1:
            time.sleep(delay)

    # --- Serialise and write ------------------------------------------------
    json_str = serialise(records)

    try:
        write_output(json_str, output_path)
        logging.info("Output written to: %s", output_path)
    except ScraperOutputError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    sys.exit(0)
