"""Configuration loader for the MoneyControl scraper.

Reads ``config.yaml`` (or a custom path) and exposes a :class:`ScraperConfig`
dataclass with typed fields.  All fields have sensible defaults so the scraper
works even without a config file.

Database credentials can be supplied via a ``.env`` file in the project root
(or via real environment variables).  Environment variables take precedence
over values in the ``database:`` section of ``config.yaml``, so you can keep
secrets out of version control.

Precedence order (highest → lowest):
  1. Environment variables / ``.env`` file
  2. ``config.yaml`` ``database:`` section
  3. Built-in defaults
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default config file location — project root / config.yaml
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
# Default .env location — project root / .env
DEFAULT_ENV_PATH = Path(__file__).parent.parent / ".env"


@dataclass
class DatabaseConfig:
    """Connection settings for the PostgreSQL database.

    Attributes:
        host:          Database server hostname or IP address.
        port:          Database server port (1–65535).
        dbname:        Name of the database to connect to.
        user:          Database user name.
        password:      Database user password.
        schema:        PostgreSQL schema name (e.g. ``stoxscoop_dev``).
        min_pool_size: Minimum number of connections in the pool (default 2).
        max_pool_size: Maximum number of connections in the pool (default 10).
    """

    host: str
    port: int
    dbname: str
    user: str
    password: str
    schema: str
    min_pool_size: int = 2
    max_pool_size: int = 10


@dataclass
class ScraperConfig:
    """Typed configuration for the scraper.

    Attributes:
        urls:        List of article URLs to scrape (from config file).
        sections:    Whitelist of section title substrings to keep.
                     Empty list means keep all sections.
        output_dir:  Directory where timestamped JSON files are saved.
        delay:       Seconds to wait between consecutive HTTP requests.
        output_mode: Where to send scraper output — ``"file"``, ``"database"``,
                     or ``"both"`` (case-insensitive). Defaults to ``"file"``.
        database:    Database connection settings; required when *output_mode*
                     is ``"database"`` or ``"both"``.
    """

    urls: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    output_dir: str = "output"
    delay: float = 1.0
    output_mode: str = "file"
    database: DatabaseConfig | None = None


def load_config(path: str | Path | None = None) -> ScraperConfig:
    """Load configuration from a YAML file, with env var overrides for DB settings.

    Falls back to :data:`DEFAULT_CONFIG_PATH` when *path* is ``None``.
    Returns a default :class:`ScraperConfig` if the file does not exist or
    cannot be parsed.

    Database credentials are merged in this order (highest wins):
      1. Environment variables (or ``.env`` file)
      2. ``database:`` section in ``config.yaml``
      3. Built-in defaults

    Args:
        path: Path to the YAML config file, or ``None`` to use the default.

    Returns:
        A populated :class:`ScraperConfig` instance.
    """
    # Load .env file into os.environ (does not overwrite existing env vars)
    _load_dotenv()

    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        logger.warning("Config file not found at %s — using defaults.", config_path)
        return ScraperConfig()

    try:
        import yaml  # type: ignore[import]
    except ImportError:
        logger.warning(
            "PyYAML is not installed — cannot read config file. "
            "Install it with: pip install pyyaml"
        )
        return ScraperConfig()

    try:
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse config file %s: %s — using defaults.", config_path, exc)
        return ScraperConfig()

    config = ScraperConfig(
        urls=_as_str_list(data.get("urls", [])),
        sections=_as_str_list(data.get("sections", [])),
        output_dir=str(data.get("output_dir", "output")),
        delay=float(data.get("delay", 1.0)),
        output_mode=str(data.get("output_mode", "file")),
        database=_parse_database_config(data.get("database")),
    )
    _validate_config(config, data)
    return config


def filter_sections(
    sections: dict[str, dict[str, str]],
    whitelist: list[str],
) -> dict[str, dict[str, str]]:
    """Return only the sections whose title matches the whitelist.

    Matching is case-insensitive substring: a section is kept if any
    whitelist entry appears anywhere in the section title.

    If *whitelist* is empty, all sections are returned unchanged.

    Args:
        sections:  The full sections dict from an :class:`OutputRecord`.
        whitelist: List of section title substrings to keep.

    Returns:
        Filtered sections dict.
    """
    if not whitelist:
        return sections

    lower_whitelist = [w.lower() for w in whitelist]
    return {
        title: stocks
        for title, stocks in sections.items()
        if any(w in title.lower() for w in lower_whitelist)
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALID_OUTPUT_MODES = {"file", "database", "both"}
_REQUIRED_DB_KEYS = ("host", "port", "dbname", "user", "password", "schema")


def _validate_config(config: ScraperConfig, raw_data: dict) -> None:
    """Validate and normalise *config* in-place.

    Normalises ``output_mode`` to lowercase, then checks:

    * ``output_mode`` is one of ``{"file", "database", "both"}``; exits with
      status 1 if not.
    * When ``output_mode`` is ``"database"`` or ``"both"``, all six required
      database keys are present — either from the YAML ``database:`` section
      or from environment variables (``DB_HOST``, ``DB_PORT``, ``DB_NAME``,
      ``DB_USER``, ``DB_PASSWORD``, ``DB_SCHEMA``); exits with status 1 for
      the first missing key found.

    Args:
        config:   The :class:`ScraperConfig` to validate (mutated in-place).
        raw_data: The raw dict loaded from the YAML file.
    """
    # 1. Normalise output_mode to lowercase.
    config.output_mode = config.output_mode.lower()

    # 2. Validate output_mode value.
    if config.output_mode not in _VALID_OUTPUT_MODES:
        logger.error(
            "Invalid output_mode %r — must be one of %s.",
            config.output_mode,
            sorted(_VALID_OUTPUT_MODES),
        )
        sys.exit(1)

    # 3. When database output is required, check all six DB keys are present
    #    from either the YAML section or environment variables.
    if config.output_mode in {"database", "both"}:
        db_yaml: dict = raw_data.get("database") or {}
        # Map of YAML key → env var name
        key_to_env = {
            "host": "DB_HOST",
            "port": "DB_PORT",
            "dbname": "DB_NAME",
            "user": "DB_USER",
            "password": "DB_PASSWORD",
            "schema": "DB_SCHEMA",
        }
        for key, env_var in key_to_env.items():
            if key not in db_yaml and not os.environ.get(env_var):
                logger.error(
                    "Missing required database configuration key: %r "
                    "(set it in config.yaml under 'database:' or as env var %s).",
                    key,
                    env_var,
                )
                sys.exit(1)


def _load_dotenv() -> None:
    """Load ``.env`` from the project root into ``os.environ``.

    Uses ``python-dotenv`` when available.  Silently skips if the package is
    not installed or the file does not exist.  Uses ``override=True`` so that
    the ``.env`` file is always re-read on hot-reload (uvicorn --reload).
    """
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv(dotenv_path=DEFAULT_ENV_PATH, override=True)
    except ImportError:
        pass  # python-dotenv not installed — env vars must be set manually


def _as_str_list(value: object) -> list[str]:
    """Coerce *value* to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value]
    return []


def _parse_database_config(data: object) -> DatabaseConfig | None:
    """Parse the ``database`` section of the YAML into a :class:`DatabaseConfig`.

    When *data* is ``None`` or not a mapping, falls back entirely to
    environment variables (``DB_HOST``, ``DB_PORT``, ``DB_NAME``, ``DB_USER``,
    ``DB_PASSWORD``, ``DB_SCHEMA``).  Individual YAML values are overridden by
    the corresponding env var when both are present.

    Returns ``None`` only when both the YAML section and all env vars are
    absent (i.e. no database configuration at all).
    """
    yaml_data: dict = data if isinstance(data, dict) else {}

    def _get(yaml_key: str, env_var: str, default: str = "") -> str:
        """Return env var value if set, else YAML value, else default."""
        return os.environ.get(env_var) or str(yaml_data.get(yaml_key, default))

    host = _get("host", "DB_HOST")
    port_str = _get("port", "DB_PORT", "0")
    dbname = _get("dbname", "DB_NAME")
    user = _get("user", "DB_USER")
    password = _get("password", "DB_PASSWORD")
    schema = _get("schema", "DB_SCHEMA")

    # If nothing was provided at all, return None
    if not any([host, dbname, user, password, schema]):
        return None

    try:
        port = int(port_str)
    except (ValueError, TypeError):
        port = 0

    min_pool = int(os.environ.get("DB_MIN_POOL_SIZE") or yaml_data.get("min_pool_size", 2))
    max_pool = int(os.environ.get("DB_MAX_POOL_SIZE") or yaml_data.get("max_pool_size", 10))

    return DatabaseConfig(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        schema=schema,
        min_pool_size=min_pool,
        max_pool_size=max_pool,
    )
