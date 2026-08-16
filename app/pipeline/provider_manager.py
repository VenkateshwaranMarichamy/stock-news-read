"""LLM Provider Manager: multi-provider fallback and rotation for the classification pipeline.

Manages a pool of LLM provider configurations, tracks per-provider runtime state
(available / rate_limited / quota_exhausted), and selects the next available provider
according to a configurable rotation strategy (priority or round-robin).

Requirements: 1.1, 1.5, 2.1, 2.3
"""

from __future__ import annotations

import enum
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderConfig:
    """Immutable configuration for a single LLM provider."""

    name: str          # unique identifier, case-sensitive
    base_url: str
    api_key: str       # resolved value (env var reference already substituted)
    model: str
    weight: int = 1    # positive integer; reserved for future weighted round-robin


class ProviderState(enum.Enum):
    """Runtime health state of a single provider."""

    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass
class _ProviderRuntime:
    """Mutable runtime state for a single provider. Internal use only."""

    config: ProviderConfig
    state: ProviderState = field(default=ProviderState.AVAILABLE)
    retry_after: datetime | None = field(default=None)  # only set when RATE_LIMITED


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigurationError(Exception):
    """Raised for invalid or incomplete LLM provider configuration."""


# ---------------------------------------------------------------------------
# Retry-delay parsing (moved from classifier.py)
# ---------------------------------------------------------------------------

_RETRY_DELAY_RE = re.compile(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def _parse_retry_delay(error_text: str) -> float | None:
    """Return retry seconds parsed from a 429 error message, or None if not parseable.

    Gemini error format: "Please retry in 36.310724774s"
    Adds +1.0s buffer to the parsed value when found.
    Returns None when no delay is found (caller decides how to handle the absence).
    """
    m = _RETRY_DELAY_RE.search(error_text)
    if m:
        try:
            return float(m.group(1)) + 1.0  # +1s buffer, same as original
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Manager stub
# ---------------------------------------------------------------------------

class LLM_Provider_Manager:
    """Manages provider configuration, state tracking, and fallback/rotation logic."""

    def __init__(
        self,
        providers: list[ProviderConfig],
        strategy: Literal["priority", "round-robin"] = "priority",
    ) -> None:
        if not providers:
            raise ConfigurationError("providers list must not be empty")

        self._providers: list[_ProviderRuntime] = [
            _ProviderRuntime(
                config=config,
                state=ProviderState.AVAILABLE,
                retry_after=None,
            )
            for config in providers
        ]
        self._strategy = strategy
        self._rr_index: int = 0

    @classmethod
    def from_config(cls, config: dict) -> "LLM_Provider_Manager":
        """Build an LLM_Provider_Manager from a parsed config.yaml dict.

        Args:
            config: Full parsed config.yaml content as a dict.

        Returns:
            A fully initialised LLM_Provider_Manager.

        Raises:
            ConfigurationError: For any invalid or incomplete configuration.

        Requirements: 1.1, 1.2, 1.4, 1.5, 6.3, 6.4
        """
        # Read the providers list
        raw_providers: list[dict] = config.get("llm_providers", [])

        if not raw_providers:
            raise ConfigurationError("No llm_providers configured in config file")

        # Read and validate the rotation strategy
        strategy: str = config.get("llm_rotation_strategy", "priority")
        if strategy not in ("priority", "round-robin"):
            raise ConfigurationError(
                f"Invalid llm_rotation_strategy: {strategy!r}. "
                f"Must be 'priority' or 'round-robin'."
            )

        # Parse each provider entry
        provider_configs: list[ProviderConfig] = []
        seen_names: dict[str, int] = {}  # name -> 1-based index

        for i, entry in enumerate(raw_providers, start=1):
            # Validate required string fields
            for field_name in ("name", "base_url", "model"):
                value = entry.get(field_name)
                if not value or not isinstance(value, str) or not value.strip():
                    raise ConfigurationError(
                        f"Provider at index {i}: missing required field '{field_name}'"
                    )

            name: str = entry["name"]
            base_url: str = entry["base_url"]
            model: str = entry["model"]

            # Resolve api_key (env var reference or literal)
            raw_api_key = entry.get("api_key", "")
            if isinstance(raw_api_key, str) and raw_api_key.startswith("$"):
                var_name = raw_api_key[1:]
                api_key = os.environ.get(var_name, "")
                if not api_key:
                    raise ConfigurationError(
                        f"Provider '{name}': env var '{var_name}' is not set or empty"
                    )
            else:
                api_key = str(raw_api_key) if raw_api_key is not None else ""

            # Validate weight if provided
            if "weight" in entry:
                weight = entry["weight"]
                if not isinstance(weight, int) or weight <= 0:
                    raise ConfigurationError(
                        f"Provider '{name}': weight must be a positive integer"
                    )
                weight_value = weight
            else:
                weight_value = 1

            # Check for duplicate names
            if name in seen_names:
                j = seen_names[name]
                raise ConfigurationError(
                    f"Duplicate provider name '{name}' at indices {j} and {i} (1-based)"
                )
            seen_names[name] = i

            provider_configs.append(
                ProviderConfig(
                    name=name,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    weight=weight_value,
                )
            )

        return cls(providers=provider_configs, strategy=strategy)  # type: ignore[arg-type]

    def get_provider_configs(self) -> list[ProviderConfig]:
        """Return the parsed provider list in config-file order."""
        return [runtime.config for runtime in self._providers]

    def get_provider_state(self, name: str) -> ProviderState:
        """Return the current state of the named provider.

        Raises ValueError if no provider with the given name is found.
        """
        for runtime in self._providers:
            if runtime.config.name == name:
                return runtime.state
        raise ValueError(f"Unknown provider name: {name!r}")

    def record_quota_error(self, name: str) -> None:
        """Transition named provider to QUOTA_EXHAUSTED.

        Requirements: 2.2, 4.2, 4.5
        """
        runtime = self._find_runtime(name)
        runtime.state = ProviderState.QUOTA_EXHAUSTED
        runtime.retry_after = None
        logger.warning("Provider '%s' marked as quota_exhausted", name)

    def record_rate_limit_error(self, name: str, error_text: str) -> None:
        """Transition named provider to RATE_LIMITED or QUOTA_EXHAUSTED.

        Calls _parse_retry_delay to extract a delay from error_text.
        - If a delay >= 1.0 s is found: state = RATE_LIMITED, retry_after = now + delay (capped at 24 h).
        - Otherwise: treat as quota exhaustion (state = QUOTA_EXHAUSTED, retry_after = None).

        Requirements: 2.3, 2.6, 4.2, 4.5
        """
        runtime = self._find_runtime(name)
        delay = _parse_retry_delay(error_text)
        if delay is not None and delay >= 1.0:
            runtime.state = ProviderState.RATE_LIMITED
            runtime.retry_after = datetime.now(tz=timezone.utc) + timedelta(
                seconds=min(delay, 86400.0)
            )
            logger.warning(
                "Provider '%s' rate_limited, retry after %s", name, runtime.retry_after
            )
        else:
            runtime.state = ProviderState.QUOTA_EXHAUSTED
            runtime.retry_after = None
            logger.warning(
                "Provider '%s' marked as quota_exhausted (no parseable retry delay)", name
            )

    # ------------------------------------------------------------------
    # Provider selection
    # ------------------------------------------------------------------

    def _recheck_rate_limits(self) -> None:
        """Lazily re-enable any RATE_LIMITED providers whose retry_after has passed.

        Requirements: 2.4, 4.4
        """
        now = datetime.now(tz=timezone.utc)
        for runtime in self._providers:
            if (
                runtime.state == ProviderState.RATE_LIMITED
                and runtime.retry_after is not None
                and runtime.retry_after <= now
            ):
                runtime.state = ProviderState.AVAILABLE
                runtime.retry_after = None
                logger.info(
                    "Provider '%s' has been re-enabled after rate limit",
                    runtime.config.name,
                )

    def get_available_provider(self) -> ProviderConfig | None:
        """Return the next provider to use according to the configured strategy.

        Lazily re-enables RATE_LIMITED providers whose retry_after has passed.
        Returns None if all providers are unavailable and emits an ERROR log.

        Requirements: 2.4, 3.2, 3.3, 3.6, 3.7, 4.1, 4.3
        """
        self._recheck_rate_limits()

        if self._strategy == "priority":
            for runtime in self._providers:
                if runtime.state == ProviderState.AVAILABLE:
                    config = runtime.config
                    logger.debug(
                        "Using provider '%s' model '%s'", config.name, config.model
                    )
                    return config
        elif self._strategy == "round-robin":
            n = len(self._providers)
            for offset in range(n):
                idx = (self._rr_index + offset) % n
                runtime = self._providers[idx]
                if runtime.state == ProviderState.AVAILABLE:
                    self._rr_index = (idx + 1) % n
                    config = runtime.config
                    logger.debug(
                        "Using provider '%s' model '%s'", config.name, config.model
                    )
                    return config

        logger.error(
            "All LLM providers exhausted — classification cannot proceed"
        )
        return None

    # ------------------------------------------------------------------
    # Reset methods
    # ------------------------------------------------------------------

    def reset_provider(self, name: str) -> None:
        """Reset named provider to AVAILABLE, clearing any retry_after timer.

        Raises ValueError if name is not found.
        Requirements: 7.1, 7.3, 7.4, 4.4
        """
        runtime = self._find_runtime(name)
        runtime.state = ProviderState.AVAILABLE
        runtime.retry_after = None
        logger.info("Provider '%s' has been re-enabled", name)

    def reset_all_providers(self) -> None:
        """Reset all providers to AVAILABLE, clearing all retry_after timers.

        Requirements: 7.2, 7.4, 4.4
        """
        for runtime in self._providers:
            runtime.state = ProviderState.AVAILABLE
            runtime.retry_after = None
            logger.info("Provider '%s' has been re-enabled", runtime.config.name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_runtime(self, name: str) -> _ProviderRuntime:
        """Return the _ProviderRuntime for the named provider.

        Raises ValueError if name is not found.
        """
        for runtime in self._providers:
            if runtime.config.name == name:
                return runtime
        raise ValueError(f"Unknown provider name: {name!r}")
