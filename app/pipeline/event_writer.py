"""Event_DB_Writer: persists classified events to PostgreSQL.

Requirements: 4.1–4.6, 5.1–5.7, 9.1–9.3

Migration note:
  To enable idempotency via ON CONFLICT DO NOTHING, run the following SQL
  once against the stoxscoop_dev schema (PostgreSQL 15+ for NULLS NOT DISTINCT):

    CREATE UNIQUE INDEX IF NOT EXISTS uq_events_idempotency
    ON stoxscoop_dev.events (stock_id, event_type, event_subtype, event_date, source_url)
    NULLS NOT DISTINCT;

  For PostgreSQL < 15, use a partial index or coalesce approach instead.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

import asyncpg

from app.pipeline.classifier import ClassificationResult
from moneycontrol_scraper.models import OutputRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DETAIL_TABLE_MAP: dict[str, str] = {
    "business": "business_event_details",
    "corporate_action": "corporate_action_details",
    "credit_rating": "credit_rating_details",
    "disclosure": "disclosure_details",
    "financials": "financial_result_details",
    "fundraising": "fundraising_details",
    "governance": "governance_details",
    "insider": "insider_details",
    "legal": "legal_details",
}

RECOGNISED_EVENT_TYPES = set(_DETAIL_TABLE_MAP.keys())

# ---------------------------------------------------------------------------
# Valid columns per detail table (to filter out LLM hallucinations)
# ---------------------------------------------------------------------------

_DETAIL_TABLE_COLUMNS: dict[str, set[str]] = {
    "business_event_details": {
        "contract_type", "client_name", "client_sector", "contract_value",
        "capex_amount", "currency", "duration_years", "geography", "project_name",
        "expected_completion", "jv_partner", "ownership_pct", "is_repeat_order",
        "description", "campaign_name", "target_revenue", "target_timeline",
        "product_name", "target_geography", "target_segment",
    },
    "corporate_action_details": {
        "record_date", "effective_date", "ratio", "amount_per_share", "total_size",
        "currency", "target_company", "swap_ratio", "offer_price", "description",
        "stake_acquired_pct", "resulting_stake_pct", "shares_transacted",
    },
    "credit_rating_details": {
        "agency", "instrument_type", "instrument_name", "rating_before", "rating_after",
        "outlook_before", "outlook_after", "rated_amount", "currency", "rationale",
        "rating_date", "description",
    },
    "disclosure_details": {
        "investor_category", "investor_name", "investor_country", "transaction_type",
        "transaction_mode", "shares_transacted", "price_per_share", "transaction_value",
        "currency", "stake_before", "stake_after", "transaction_date", "exchange",
        "description",
    },
    "financial_result_details": {
        "period_quarter", "period_year", "revenue", "revenue_yoy_pct", "ebitda",
        "ebitda_margin", "pat", "pat_yoy_pct", "eps", "beat_miss",
        "guidance_revenue", "guidance_margin", "currency", "key_highlight", "description",
    },
    "fundraising_details": {
        "issue_size", "currency", "price_per_share", "number_of_shares",
        "allottee_name", "allottee_category", "coupon_rate", "maturity_date",
        "tenure_years", "purpose", "open_date", "close_date", "subscription_times",
        "description",
    },
    "governance_details": {
        "person_name", "designation", "change_type", "effective_date", "reason",
        "regulator", "action_type", "penalty_amount", "currency", "meeting_date",
        "agenda_summary", "description",
    },
    "insider_details": {
        "person_name", "designation", "relationship", "transaction_type",
        "shares_transacted", "price_per_share", "transaction_value", "currency",
        "stake_before", "stake_after", "pledge_percentage", "sebi_disclosure_date",
        "transaction_date", "description",
    },
    "legal_details": {
        "forum", "case_number", "counterparty", "demand_amount", "penalty_amount",
        "currency", "company_stance", "outcome", "order_date", "next_hearing_date",
        "contingent_liability", "description",
    },
}

# NOT NULL columns that need a fallback when LLM returns null
_NOT_NULL_FALLBACKS: dict[str, dict[str, str]] = {
    "disclosure_details": {"investor_category": "other", "transaction_type": "buy"},
    "insider_details": {"transaction_type": "buy", "person_name": "Unknown"},
}

# Valid enum values per column — map invalid LLM values to safe fallbacks
_ENUM_COLUMN_MAP: dict[str, tuple[set[str], str]] = {
    "investor_category": (
        {"superinvestor", "fii", "dii", "hni", "mutual_fund", "insurance", "corporate_body", "other"},
        "other",
    ),
    "transaction_type": (
        {"buy", "sell", "increase", "decrease", "pledge", "revoke"},
        "buy",
    ),
    "transaction_mode": (
        {"bulk_deal", "block_deal", "open_market", "off_market"},
        None,  # nullable — set to None if invalid
    ),
    "beat_miss": (
        {"beat", "miss", "inline"},
        None,
    ),
    "outcome": (
        {"favorable", "adverse", "pending", "settled"},
        "pending",
    ),
    "agency": (
        {"crisil", "icra", "care", "india_ratings", "fitch", "moodys", "sp_global"},
        None,
    ),
    "contract_type": (
        {"mou", "loi", "confirmed", "partnership", "renewal", "amendment"},
        None,
    ),
}

# Columns with varchar length limits that need truncation
_VARCHAR_LIMITS: dict[str, int] = {
    "change_type": 30,
    "ratio": 30,
    "swap_ratio": 30,
    "period_quarter": 5,
    "company_stance": 20,
    "currency": 3,
    "exchange": 10,
    "allottee_category": 50,
}


def _truncate_varchar(col: str, val: object) -> object:
    """Truncate string values to their column's varchar limit if defined."""
    if col in _VARCHAR_LIMITS and isinstance(val, str):
        limit = _VARCHAR_LIMITS[col]
        if len(val) > limit:
            return val[:limit]
    return val


# Columns that hold a single date value — used by _parse_date_value
_DATE_COLUMNS: set[str] = {
    "record_date", "effective_date", "rating_date", "transaction_date",
    "sebi_disclosure_date", "order_date", "next_hearing_date",
    "maturity_date", "open_date", "close_date", "expected_completion",
    "meeting_date",
}

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _parse_date_value(val: object) -> date | None:
    """Convert an LLM date value to a Python date, or None on failure.

    Handles:
    - Python date/datetime objects (passthrough)
    - ISO 8601 strings: "2024-05-21"
    - Comma-separated date strings: "2024-05-21, 2024-05-27, 2024-07-06" → earliest date
    - Lists of date strings → earliest date
    - Anything else → None (silently)
    """
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, list):
        # Pick earliest from a list of date strings
        candidates = [_parse_date_value(v) for v in val]
        parsed = [d for d in candidates if d is not None]
        return min(parsed) if parsed else None
    if isinstance(val, str):
        # Extract all ISO dates found in the string, return the earliest
        matches = _DATE_RE.findall(val)
        parsed = []
        for m in matches:
            try:
                parsed.append(date.fromisoformat(m))
            except ValueError:
                pass
        return min(parsed) if parsed else None
    return None


def _sanitise_details(detail_table: str, details: dict) -> dict:
    """Sanitise LLM-generated detail fields before DB insertion.

    1. Merges numbered person_name variants (person_name_2, person_name_3...)
       into the single person_name field as comma-separated values.
    2. Converts list values for string columns into comma-separated strings.
    3. Strips columns not in the table schema (prevents 'column does not exist').
    4. Maps invalid enum values to safe fallbacks (prevents enum constraint errors).
    5. Applies NOT NULL fallbacks for required columns.
    """
    valid_cols = _DETAIL_TABLE_COLUMNS.get(detail_table, set())
    not_null_fallbacks = _NOT_NULL_FALLBACKS.get(detail_table, {})

    # Step 1: Collect and merge numbered person_name_N variants
    person_name_parts: list[str] = []
    extra_person_keys: set[str] = set()
    for col in list(details.keys()):
        if col == "person_name":
            val = details[col]
            if val:
                person_name_parts.insert(0, str(val).strip())
        elif col.startswith("person_name_") and col[len("person_name_"):].isdigit():
            val = details[col]
            if val:
                person_name_parts.append(str(val).strip())
            extra_person_keys.add(col)

    sanitised: dict = {}

    for col, val in details.items():
        # Skip numbered person_name variants (already merged above)
        if col in extra_person_keys:
            continue

        # Merge collected person_name parts
        if col == "person_name" and person_name_parts:
            val = ", ".join(p for p in person_name_parts if p)

        # Step 2: Convert list values to comma-separated string for text columns
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val if v is not None) or None

        # Step 3: Skip columns not in the schema
        if col not in valid_cols:
            logger.debug("Skipping unknown column '%s' for table %s", col, detail_table)
            continue

        # Step 4: Map invalid enum values
        if col in _ENUM_COLUMN_MAP and isinstance(val, str):
            valid_vals, fallback = _ENUM_COLUMN_MAP[col]
            if val not in valid_vals:
                logger.debug(
                    "Invalid enum value '%s' for column '%s' — using fallback '%s'",
                    val, col, fallback,
                )
                val = fallback

        # Step 4b: Parse date columns — convert strings/lists to Python date
        if col in _DATE_COLUMNS and not isinstance(val, date):
            # If it's a list, take the first element
            if isinstance(val, list):
                val = val[0] if val else None
            val = _parse_date_value(val)

        # Step 4c: Truncate varchar columns to their DB limits
        val = _truncate_varchar(col, val)

        # Step 4c: Strip percentage signs and clean numeric columns
        if val is not None and not isinstance(val, (int, float, date, datetime, bool)):
            str_val = str(val).strip()
            # Remove trailing % from numeric fields
            if str_val.endswith('%'):
                str_val = str_val[:-1].strip()
                try:
                    val = float(str_val)
                except ValueError:
                    val = None
            # Remove currency symbols and commas from numeric-looking strings
            elif str_val and not isinstance(val, str) is False:
                # Only attempt conversion if the column is NOT a known text column
                _TEXT_COLS = {
                    "client_name", "client_sector", "geography", "project_name",
                    "jv_partner", "target_geography", "target_segment", "campaign_name",
                    "target_timeline", "product_name", "description", "person_name",
                    "designation", "change_type", "reason", "regulator", "action_type",
                    "agenda_summary", "investor_name", "investor_country", "exchange",
                    "target_company", "rationale", "instrument_name", "instrument_type",
                    "rating_before", "rating_after", "outlook_before", "outlook_after",
                    "ratio", "swap_ratio", "guidance_revenue", "guidance_margin",
                    "key_highlight", "forum", "case_number", "counterparty",
                    "company_stance", "allottee_name", "allottee_category", "purpose",
                    "relationship", "currency", "period_quarter", "agency",
                    "investor_category", "transaction_type", "transaction_mode",
                    "beat_miss", "outcome", "contract_type",
                }
                if col not in _TEXT_COLS and isinstance(val, str):
                    clean_num = str_val.replace(',', '').replace('₹', '').replace('$', '').strip()
                    if clean_num and col not in _DATE_COLUMNS:
                        try:
                            val = float(clean_num)
                        except ValueError:
                            pass  # keep original string; let DB handle/reject it

        sanitised[col] = val

    # Step 5: Apply NOT NULL fallbacks for missing/null required columns
    for col, fallback in not_null_fallbacks.items():
        if col not in sanitised or sanitised[col] is None:
            sanitised[col] = fallback

    return sanitised


# ---------------------------------------------------------------------------
# Enum value helpers — map LLM output to valid DB enum values
# ---------------------------------------------------------------------------

def _safe_sentiment(value: str | None) -> str:
    """Map LLM sentiment to a valid sentiment_enum value.

    DB enum: positive, negative, neutral  (no 'mixed')
    'mixed' → 'neutral' as the closest safe fallback.
    """
    valid = {"positive", "negative", "neutral"}
    if value in valid:
        return value
    return "neutral"


def _safe_priority(value: str | None) -> str:
    """Map LLM priority to a valid priority_enum value.

    DB enum: low, medium, high  (no 'critical')
    'critical' → 'high' as the closest safe fallback.
    """
    valid = {"low", "medium", "high"}
    if value in valid:
        return value
    if value == "critical":
        return "high"
    return "low"

class Event_DB_Writer:
    """Persists event batches, events, and detail rows to PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._schema = schema

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detail_table(event_type: str) -> str:
        """Return the detail table name for the given event_type.

        Raises ValueError for unrecognised types.
        """
        try:
            return _DETAIL_TABLE_MAP[event_type]
        except KeyError:
            raise ValueError(f"Unrecognised event_type: {event_type!r}")

    # ------------------------------------------------------------------
    # Batch management
    # ------------------------------------------------------------------

    async def start_batch(self, batch_name: str) -> int:
        """INSERT into event_batches; return batch_id.

        Raises RuntimeError on failure.
        """
        sql = (
            f"INSERT INTO {self._schema}.event_batches"
            " (batch_name, ingestion_source, started_at, total_events)"
            " VALUES ($1, 'scraper', $2, 0)"
            " RETURNING id"
        )
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(sql, batch_name, datetime.now(timezone.utc))
            return row["id"]
        except Exception as exc:
            raise RuntimeError(f"Failed to create event batch: {exc}") from exc

    async def finish_batch(self, batch_id: int, total_events: int) -> None:
        """UPDATE event_batches SET completed_at, total_events.

        Logs error on failure; does not re-raise.
        """
        sql = (
            f"UPDATE {self._schema}.event_batches"
            " SET completed_at = $1, total_events = $2"
            " WHERE id = $3"
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(sql, datetime.now(timezone.utc), total_events, batch_id)
        except Exception as exc:
            logger.error("Failed to update batch %d: %s", batch_id, exc)

    # ------------------------------------------------------------------
    # Event writing
    # ------------------------------------------------------------------

    def _build_event_params(
        self,
        result: ClassificationResult,
        stock_id: int,
        batch_id: int,
        source_url: str,
        published_date: str | None = None,
    ) -> dict:
        """Build the column-value dict for the events INSERT.

        Always sets source_name='moneycontrol', ingestion_source='moneycontrol',
        is_verified=False. Applies confidence null-coercion (Req 5.7).
        Uses published_date fallback for event_date (Req 5.4).
        """
        # Confidence null-coercion: if score is not null but version is null, null both
        confidence_score = result.confidence_score
        confidence_model_version = result.confidence_model_version
        if confidence_score is not None and confidence_model_version is None:
            confidence_score = None
            confidence_model_version = None

        # event_date: use result value or fall back to published_date
        event_date_str = result.event_date
        if not event_date_str and published_date:
            event_date_str = published_date

        # Parse event_date to a Python date object
        event_date = None
        if event_date_str:
            try:
                from datetime import date
                event_date = date.fromisoformat(event_date_str)
            except (ValueError, TypeError):
                if published_date:
                    try:
                        from datetime import date
                        event_date = date.fromisoformat(published_date)
                    except (ValueError, TypeError):
                        event_date = None

        return {
            "stock_id": stock_id,
            "batch_id": batch_id,
            "event_type": result.event_type,
            "event_subtype": result.event_subtype,
            "signal_type": result.signal_type or "neutral",
            "signal_reason": result.signal_reason,
            "sentiment": _safe_sentiment(result.sentiment),
            "priority": _safe_priority(result.priority),
            "confidence_score": confidence_score,
            "confidence_model_version": confidence_model_version,
            "tags": result.tags or [],
            "title": result.title or "",
            "summary": result.summary,
            "event_date": event_date,
            "source_url": source_url,
            "source_name": "moneycontrol",
            "ingestion_source": "scraper",
            "is_verified": False,
        }

    async def write_event(
        self,
        batch_id: int,
        stock_id: int,
        result: ClassificationResult,
        record: OutputRecord,
        source_url: str,
    ) -> bool:
        """Insert events row + detail row in a single transaction.

        Returns True on full success (events + complete detail row).
        Returns False on:
          - skip (duplicate)
          - events insert failure
          - detail insert fell back to minimal row (partial success — event row
            committed but detail data lost; counted as db_failure so stocks_failed
            increments correctly)
        """
        event_type = result.event_type
        if event_type not in RECOGNISED_EVENT_TYPES:
            logger.warning(
                "Unrecognised event_type '%s' for stock_id=%d — skipping insertion",
                event_type, stock_id,
            )
            return False

        detail_table = self._detail_table(event_type)
        params = self._build_event_params(
            result, stock_id, batch_id, source_url, record.date
        )

        events_sql = (
            f"INSERT INTO {self._schema}.events"
            " (stock_id, batch_id, event_type, event_subtype, signal_type, signal_reason,"
            "  sentiment, priority, confidence_score, confidence_model_version,"
            "  tags, title, summary, event_date, source_url, source_name,"
            "  ingestion_source, is_verified)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)"
            " ON CONFLICT DO NOTHING"
            " RETURNING id"
        )

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        events_sql,
                        params["stock_id"],
                        params["batch_id"],
                        params["event_type"],
                        params["event_subtype"],
                        params["signal_type"],
                        params["signal_reason"],
                        params["sentiment"],
                        params["priority"],
                        params["confidence_score"],
                        params["confidence_model_version"],
                        params["tags"],
                        params["title"],
                        params["summary"],
                        params["event_date"],
                        params["source_url"],
                        params["source_name"],
                        params["ingestion_source"],
                        params["is_verified"],
                    )

                    if row is None:
                        # ON CONFLICT DO NOTHING — duplicate
                        logger.info(
                            "Duplicate skipped: stock_id=%d url=%s type=%s subtype=%s date=%s",
                            stock_id, source_url, event_type,
                            result.event_subtype, params["event_date"],
                        )
                        return False

                    event_id = row["id"]

                    # Insert detail row using a savepoint so a detail failure
                    # doesn't abort the outer transaction.
                    # detail_ok=True means full detail row inserted.
                    detail_ok = False
                    async with conn.transaction():
                        detail_ok = await self._insert_detail(
                            conn, detail_table, event_id, result.details
                        )

            # Return True only when the full detail row was inserted.
            # Minimal-row fallback counts as a partial failure (db_failures++).
            return detail_ok

        except Exception as exc:
            logger.error(
                "write_event failed for stock_id=%d batch_id=%d event_type=%s: %s",
                stock_id, batch_id, event_type, exc,
            )
            return False

    async def _insert_detail(
        self,
        conn: asyncpg.Connection,
        detail_table: str,
        event_id: int,
        details: dict,
    ) -> bool:
        """Insert a row into the detail table for the given event_id.

        Returns True if the full detail row was inserted.
        Returns False if fell back to minimal row (event_id only).
        """
        # Sanitise: strip unknown columns, fix enum values, apply NOT NULL fallbacks
        clean = _sanitise_details(detail_table, details) if details else {}

        if not clean:
            # Insert minimal row satisfying NOT NULL constraints
            not_null_fallbacks = _NOT_NULL_FALLBACKS.get(detail_table, {})
            if not_null_fallbacks:
                min_cols = ["event_id"] + list(not_null_fallbacks.keys())
                min_placeholders = ["$1"] + [f"${i+2}" for i in range(len(not_null_fallbacks))]
                min_values = [event_id] + list(not_null_fallbacks.values())
                min_sql = (
                    f"INSERT INTO {self._schema}.{detail_table}"
                    f" ({', '.join(min_cols)})"
                    f" VALUES ({', '.join(min_placeholders)})"
                    " ON CONFLICT (event_id) DO NOTHING"
                )
                await conn.execute(min_sql, *min_values)
            else:
                await conn.execute(
                    f"INSERT INTO {self._schema}.{detail_table} (event_id) VALUES ($1)"
                    " ON CONFLICT (event_id) DO NOTHING",
                    event_id,
                )
            return False

        # Build dynamic INSERT from sanitised dict
        columns = ["event_id"] + list(clean.keys())
        placeholders = ["$1"] + [f"${i+2}" for i in range(len(clean))]
        values = [event_id] + list(clean.values())

        sql = (
            f"INSERT INTO {self._schema}.{detail_table}"
            f" ({', '.join(columns)})"
            f" VALUES ({', '.join(placeholders)})"
            " ON CONFLICT (event_id) DO NOTHING"
        )
        try:
            await conn.execute(sql, *values)
            return True
        except Exception as exc:
            logger.warning(
                "Detail insert failed for event_id=%d table=%s: %s — inserting minimal row",
                event_id, detail_table, exc,
            )
            # Build minimal row that satisfies NOT NULL constraints
            not_null_fallbacks = _NOT_NULL_FALLBACKS.get(detail_table, {})
            if not_null_fallbacks:
                min_cols = ["event_id"] + list(not_null_fallbacks.keys())
                min_placeholders = ["$1"] + [f"${i+2}" for i in range(len(not_null_fallbacks))]
                min_values = [event_id] + list(not_null_fallbacks.values())
                min_sql = (
                    f"INSERT INTO {self._schema}.{detail_table}"
                    f" ({', '.join(min_cols)})"
                    f" VALUES ({', '.join(min_placeholders)})"
                    " ON CONFLICT (event_id) DO NOTHING"
                )
                await conn.execute(min_sql, *min_values)
            else:
                await conn.execute(
                    f"INSERT INTO {self._schema}.{detail_table} (event_id) VALUES ($1)"
                    " ON CONFLICT (event_id) DO NOTHING",
                    event_id,
                )
            return False
