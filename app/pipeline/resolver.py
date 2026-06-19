"""Stock_Name_Resolver: fuzzy-matches scraped stock names to ticker IDs.

Requirements: 1.1–1.7
"""

from __future__ import annotations

import logging
import re
import string

import asyncpg
from rapidfuzz.fuzz import token_set_ratio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEGAL_SUFFIXES = ("limited", "ltd", "industries", "inc", "corp")

# Compiled regex: matches legal suffixes as standalone tokens (word boundaries)
# Applied after lowercasing and punctuation removal
_SUFFIX_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in LEGAL_SUFFIXES) + r")\b"
)

# Punctuation removal: replace all punctuation chars with a space
_PUNCT_TABLE = str.maketrans(string.punctuation, " " * len(string.punctuation))

FUZZY_THRESHOLD = 80


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class Stock_Name_Resolver:
    """Maps scraped stock names to classification.ticker_symbol IDs."""

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._schema = schema
        self._cache: list[dict] = []

    # ------------------------------------------------------------------
    # Cache loading
    # ------------------------------------------------------------------

    async def load_cache(self) -> None:
        """Load all rows from classification.ticker_symbol into memory.

        Raises RuntimeError on DB failure (halts pipeline).
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, trading_symbol, short_name, name, is_active"
                    " FROM classification.ticker_symbol"
                )
            self._cache = [dict(row) for row in rows]
            logger.info("Loaded %d ticker symbols into cache", len(self._cache))
        except Exception as exc:
            raise RuntimeError(f"Failed to load ticker cache: {exc}") from exc

    # ------------------------------------------------------------------
    # Pure helpers
    # ------------------------------------------------------------------

    def normalise(self, name: str | None) -> str:
        """Normalise a stock name for matching.

        Steps:
          1. Return "" for None or empty input.
          2. Lowercase.
          3. Remove punctuation (replace with spaces).
          4. Strip legal suffixes as standalone tokens.
          5. Collapse consecutive whitespace.
          6. Strip leading/trailing whitespace.
        """
        if not name:
            return ""
        result = name.lower()
        result = result.translate(_PUNCT_TABLE)
        result = _SUFFIX_PATTERN.sub(" ", result)
        # Collapse consecutive whitespace
        result = " ".join(result.split())
        return result

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, scraped_name: str | None) -> int | None:
        """Return ticker_symbol.id or None.

        1. Normalise scraped_name.
        2. Exact match: short_name → trading_symbol → name (all normalised).
        3. Fuzzy match (token_set_ratio ≥ 80) against active records only.
        4. Return None and log warning if unresolved.
        """
        norm = self.normalise(scraped_name)
        if not norm:
            logger.warning(
                "Unresolved stock '%s': empty after normalisation", scraped_name
            )
            return None

        # --- Exact match pass (all records, column priority order) ---
        for col in ("short_name", "trading_symbol", "name"):
            for row in self._cache:
                val = row.get(col)
                if val and self.normalise(val) == norm:
                    return row["id"]

        # --- Fuzzy match pass (active records only) ---
        best_score = -1.0
        best_id: int | None = None
        best_name: str = ""

        for row in self._cache:
            if not row.get("is_active"):
                continue
            for col in ("short_name", "name"):
                val = row.get(col)
                if not val:
                    continue
                score = token_set_ratio(norm, self.normalise(val))
                if score > best_score or (score == best_score and row["id"] < (best_id or float("inf"))):
                    best_score = score
                    best_id = row["id"]
                    best_name = val

        if best_score >= FUZZY_THRESHOLD and best_id is not None:
            return best_id

        logger.warning(
            "Unresolved stock '%s': best candidate '%s' score=%.1f",
            scraped_name, best_name, best_score,
        )
        return None
