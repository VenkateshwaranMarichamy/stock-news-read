# Implementation Plan: LLM Provider Fallback

## Overview

Implement the `LLM_Provider_Manager` component and integrate it with `Event_Classifier` to enable
multi-provider fallback and rotation. The work is split into three phases: building and testing
the provider manager in isolation, migrating `_parse_retry_delay`, and wiring everything into the
classifier with backward compatibility preserved.

## Tasks

- [x] 1. Set up module structure and core data types
  - Create `app/pipeline/provider_manager.py` with `ProviderConfig` frozen dataclass, `ProviderState` enum, `_ProviderRuntime` internal dataclass, and `ConfigurationError` exception class
  - Create `tests/pipeline/test_provider_manager.py` with test file scaffold (imports, fixtures, placeholder)
  - Ensure `app/pipeline/__init__.py` exports the new public symbols (`ProviderConfig`, `ProviderState`, `LLM_Provider_Manager`, `ConfigurationError`)
  - _Requirements: 1.1, 1.5, 2.1_

- [x] 2. Implement `_parse_retry_delay` in provider_manager.py
  - [x] 2.1 Move `_parse_retry_delay` from `classifier.py` to `provider_manager.py` as a module-level private function
    - Change return type from `float` to `float | None` (drop `default` param; return `None` when no delay found)
    - Add `+1.0` buffer on the parsed value (same as original)
    - Add re-export shim in `classifier.py` to avoid breaking any existing imports
    - _Requirements: 2.3_

  - [ ]* 2.2 Write unit tests for `_parse_retry_delay`
    - Test message with parseable seconds → returns float + 1.0 buffer
    - Test message with no numeric delay → returns `None`
    - Test edge cases: delay < 1 s, non-numeric text, empty string
    - _Requirements: 2.3, 2.6_

- [x] 3. Implement `LLM_Provider_Manager.from_config()` with config validation
  - [x] 3.1 Implement `from_config(cls, config: dict) -> LLM_Provider_Manager`
    - Parse `llm_providers` list into `ProviderConfig` objects; resolve `$ENV_VAR` api_key references
    - Parse `llm_rotation_strategy` (default `"priority"`); validate it is `"priority"` or `"round-robin"`
    - Validate required fields (`name`, `base_url`, `model`) with 1-based index in error messages
    - Detect and reject duplicate `name` values (case-sensitive); include both 1-based indices in error
    - Validate `weight > 0` if provided
    - Raise `ConfigurationError` with descriptive message for all invalid inputs
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 6.3, 6.4_

  - [ ]* 3.2 Write property test for config round-trip (Property 2)
    - **Property 2: Config parsing preserves order and field values (round-trip)**
    - **Validates: Requirements 6.2**

  - [ ]* 3.3 Write property test for duplicate names raising ConfigurationError (Property 10)
    - **Property 10: Duplicate provider names raise `ConfigurationError`**
    - **Validates: Requirements 6.4**

  - [ ]* 3.4 Write property test for missing required fields raising ConfigurationError (Property 11)
    - **Property 11: Missing required fields raise `ConfigurationError` with correct index**
    - **Validates: Requirements 1.5**

  - [ ]* 3.5 Write unit tests for `from_config()`
    - Valid config dict → correct `ProviderConfig` fields populated
    - `api_key: "$MY_KEY"` with env var set → resolved value stored
    - `api_key: "$MY_KEY"` with env var absent/empty → `ConfigurationError` naming provider and var
    - `llm_rotation_strategy: "round-robin"` → strategy set correctly
    - Unknown strategy value → `ConfigurationError`
    - Missing `name` → `ConfigurationError` with field name and 1-based index
    - Missing `base_url` → `ConfigurationError` with field name and 1-based index
    - Missing `model` → `ConfigurationError` with field name and 1-based index
    - Duplicate names → `ConfigurationError` with both 1-based indices
    - `weight: 0` → `ConfigurationError`
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 6.3, 6.4_

- [x] 4. Implement provider state initialisation and introspection
  - [x] 4.1 Implement `__init__`, `get_provider_configs()`, and `get_provider_state()`
    - `__init__` initialises all `_ProviderRuntime` entries with `state=AVAILABLE`, `retry_after=None`
    - `get_provider_configs()` returns list in config-file order
    - `get_provider_state(name)` returns current `ProviderState` for named provider
    - _Requirements: 2.1, 6.1_

  - [ ]* 4.2 Write property test for all providers initialising to available (Property 1)
    - **Property 1: All providers initialise to `available`**
    - **Validates: Requirements 2.1**

- [x] 5. Implement error recording (`record_quota_error`, `record_rate_limit_error`)
  - [x] 5.1 Implement `record_quota_error(name: str)` and `record_rate_limit_error(name: str, error_text: str)`
    - `record_quota_error` → transitions provider to `QUOTA_EXHAUSTED`
    - `record_rate_limit_error` → calls `_parse_retry_delay`; if ≥ 1 s found → `RATE_LIMITED` with `retry_after = now + delay`, capped at 24 h; if no parseable delay → `QUOTA_EXHAUSTED`
    - Emit WARNING-level log on state change (provider name, reason, no api_key in output)
    - _Requirements: 2.2, 2.3, 2.6, 4.2, 4.5_

  - [ ]* 5.2 Write property test for rate-limit retry-after capped at 24 hours (Property 6)
    - **Property 6: Rate-limit with valid delay sets retry-after ≤ 24 hours from now**
    - **Validates: Requirements 2.3**

  - [ ]* 5.3 Write unit tests for error recording
    - `record_quota_error` → state becomes `QUOTA_EXHAUSTED`
    - `record_rate_limit_error` with parseable delay → `RATE_LIMITED`, `retry_after` set correctly
    - `record_rate_limit_error` with no parseable delay → `QUOTA_EXHAUSTED`
    - WARNING log emitted in both cases
    - No `api_key` value appears in any log output
    - _Requirements: 2.2, 2.3, 2.6, 4.2, 4.5_

- [x] 6. Implement reset methods
  - [x] 6.1 Implement `reset_provider(name: str)` and `reset_all_providers()`
    - `reset_provider` → state = `AVAILABLE`, `retry_after = None`; raise `ValueError` for unknown name
    - `reset_all_providers` → all providers set to `AVAILABLE`, all `retry_after` cleared
    - Emit INFO-level log when a provider is re-enabled
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 4.4_

  - [ ]* 6.2 Write property test for `reset_provider` idempotency (Property 8)
    - **Property 8: `reset_provider` is idempotent for any initial state**
    - **Validates: Requirements 7.1, 7.4**

  - [ ]* 6.3 Write property test for `reset_all_providers` (Property 9)
    - **Property 9: `reset_all_providers` leaves every provider available**
    - **Validates: Requirements 7.2**

  - [ ]* 6.4 Write unit tests for reset methods
    - `reset_provider` on `QUOTA_EXHAUSTED` → `AVAILABLE`, `retry_after = None`
    - `reset_provider` on `RATE_LIMITED` → `AVAILABLE`, `retry_after = None`
    - `reset_provider` on already `AVAILABLE` → no error, stays `AVAILABLE`
    - `reset_provider` with unknown name → `ValueError`
    - `reset_all_providers` → all `AVAILABLE`
    - INFO log emitted for re-enabled providers
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 4.4_

- [x] 7. Checkpoint — core state management complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement provider selection (`get_available_provider`)
  - [x] 8.1 Implement `_recheck_rate_limits()` helper and `get_available_provider()` with priority strategy
    - `_recheck_rate_limits` iterates providers; transitions any `RATE_LIMITED` provider whose `retry_after` has passed back to `AVAILABLE` and emits INFO log
    - Priority strategy: scan from index 0, return first `AVAILABLE` provider
    - Return `None` if all unavailable; emit ERROR log in that case
    - _Requirements: 2.4, 3.2, 3.6, 3.7, 4.1, 4.3_

  - [x] 8.2 Implement round-robin strategy in `get_available_provider()`
    - Scan from `_rr_index`, find next `AVAILABLE` provider in circular order, advance `_rr_index` past the chosen provider
    - Return `None` if full circle finds no available provider
    - _Requirements: 3.3, 3.6_

  - [ ]* 8.3 Write property test for priority strategy (Property 3)
    - **Property 3: Priority strategy always selects the lowest-index available provider**
    - **Validates: Requirements 3.2**

  - [ ]* 8.4 Write property test for round-robin cycling (Property 4)
    - **Property 4: Round-robin strategy advances the pointer in circular order**
    - **Validates: Requirements 3.3**

  - [ ]* 8.5 Write property test for quota-exhausted never selected (Property 5)
    - **Property 5: Quota-exhausted provider is never selected until reset**
    - **Validates: Requirements 2.2, 3.5**

  - [ ]* 8.6 Write property test for all-exhausted returns None (Property 7)
    - **Property 7: All providers exhausted returns `None` from selection**
    - **Validates: Requirements 2.5, 3.7**

  - [ ]* 8.7 Write unit tests for `get_available_provider()`
    - Single provider, `AVAILABLE` → returned
    - First provider exhausted, second available → returns second (priority)
    - All providers exhausted → `None`, ERROR log emitted
    - Rate-limited provider whose timer has passed → re-enabled and returned
    - Rate-limited provider whose timer has NOT passed → skipped
    - Round-robin cycles correctly across multiple calls
    - DEBUG log emitted with provider name and model (no api_key)
    - _Requirements: 2.4, 3.2, 3.3, 3.6, 3.7, 4.1, 4.3_

- [x] 9. Checkpoint — provider manager fully functional
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Add `_make_default_provider_manager()` to `classifier.py` and update `Event_Classifier.__init__`
  - [x] 10.1 Implement `_make_default_provider_manager()` module-level helper in `classifier.py`
    - Reads `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` from environment
    - Constructs a single-provider `LLM_Provider_Manager` (strategy `"priority"`)
    - Raises `ConfigurationError` if any env var is absent (matches existing behaviour)
    - _Requirements: 1.3, 5.2_

  - [x] 10.2 Update `Event_Classifier.__init__` to accept optional `provider_manager` parameter
    - Add `provider_manager: LLM_Provider_Manager | None = None` parameter
    - When `None`, call `_make_default_provider_manager()` to build the default manager
    - Store as `self._provider_manager`
    - No changes to any call sites in `scrape.py`, `classify.py`, or the orchestrator
    - _Requirements: 5.1, 5.2_

  - [ ]* 10.3 Write unit tests for backward-compatible constructor
    - No `provider_manager` arg → `_make_default_provider_manager()` called, env vars used
    - Explicit `provider_manager` arg → stored directly, no env var read
    - `classify()` signature unchanged
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 11. Refactor `Event_Classifier.classify()` to use the provider fallback loop
  - [x] 11.1 Replace the single-provider LLM call in `classify()` with the provider fallback loop
    - Call `self._provider_manager.get_available_provider()` at the top of the loop
    - Construct `openai.AsyncOpenAI` from the selected provider's `base_url` and `api_key`
    - On `openai.RateLimitError`: call `record_rate_limit_error(name, str(exc))`, `continue` to next fallback iteration
    - On quota/exhaustion error (detect via existing `_QuotaExhaustedError` or equivalent): call `record_quota_error(name)`, `continue`
    - When `get_available_provider()` returns `None`: return `ClassificationResult(status="failed", details={"reason": "all_providers_exhausted"}, ...)`
    - Preserve the existing 3-attempt network retry loop for transient connection/timeout errors within each provider
    - _Requirements: 3.1, 3.4, 3.5, 3.7, 2.5_

  - [x] 11.2 Handle `ClassificationResult` fields when all providers exhausted
    - Return `ClassificationResult` with `status="failed"`, all typed fields `None`, `details={}`, `tags=[]`
    - _Requirements: 5.4, 5.5_

  - [ ]* 11.3 Write unit tests for fallback loop in `test_classifier.py`
    - Injected manager returning `None` → `classify()` returns `status="failed"` with `details={"reason": "all_providers_exhausted"}`
    - First provider raises `RateLimitError` → `record_rate_limit_error` called, second provider used, valid result returned
    - All providers raise quota errors → `status="failed"`
    - Existing tests continue to pass with zero-arg `Event_Classifier` construction
    - _Requirements: 3.1, 3.4, 5.1, 5.4_

- [x] 12. Update `config.yaml` with documented provider examples
  - Add commented `llm_providers` and `llm_rotation_strategy` keys to `config.yaml` matching the schema in the design
  - Keep existing keys and values unchanged
  - _Requirements: 1.1, 1.4_

- [x] 13. Final checkpoint — all integration points verified
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at meaningful boundaries
- Property tests (Properties 1–11) validate universal correctness guarantees using Hypothesis (already in dev extras)
- Unit tests validate specific examples and edge cases
- `_parse_retry_delay` must be migrated (task 2.1) before error-recording methods (task 5.1) are implemented
- No new dependencies are needed; `hypothesis` is already available in the dev extras

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "4.1"] },
    { "id": 2, "tasks": ["2.2", "3.1", "4.2"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "3.5", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "6.1"] },
    { "id": 5, "tasks": ["6.2", "6.3", "6.4", "8.1"] },
    { "id": 6, "tasks": ["8.2", "8.3", "8.5", "8.6", "8.7"] },
    { "id": 7, "tasks": ["8.4", "10.1"] },
    { "id": 8, "tasks": ["10.2", "10.3"] },
    { "id": 9, "tasks": ["11.1"] },
    { "id": 10, "tasks": ["11.2", "11.3", "12"] }
  ]
}
```
