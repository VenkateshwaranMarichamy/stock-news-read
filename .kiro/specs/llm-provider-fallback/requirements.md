# Requirements Document

## Introduction

The `stock-news-read` pipeline uses a single LLM provider (Gemini free tier) via `Event_Classifier`
to classify stock news articles. When this provider exhausts its daily quota or hits a rate limit,
all classification attempts fail with `status="failed"`, halting the pipeline.

This feature introduces a multi-provider LLM fallback and rotation system. The
`LLM_Provider_Manager` maintains an ordered list of configured providers and, when the active
provider signals quota exhaustion or a persistent rate limit, transparently routes subsequent
requests to the next available provider. The existing `Event_Classifier` interface remains
unchanged.

---

## Glossary

- **LLM_Provider_Manager**: The new component responsible for managing provider configuration,
  routing, fallback logic, and provider-state tracking.
- **Provider**: A single LLM endpoint configuration consisting of a `base_url`, `api_key`, and
  `model` name. Providers are identified by a unique `name` within the configuration.
- **Provider_List**: The ordered sequence of configured providers. Position in the list determines
  fallback priority.
- **Active_Provider**: The provider currently selected by `LLM_Provider_Manager` to serve
  classification requests.
- **Quota_Error**: An error response indicating the provider's daily or per-minute request quota
  has been exhausted (HTTP 429 with a quota-exhaustion body, or equivalent).
- **Rate_Limit_Error**: An error response indicating temporary throttling that may resolve after
  a retry delay (HTTP 429 with a `retry after` hint).
- **Fallback**: The act of demoting the `Active_Provider` and promoting the next provider in the
  `Provider_List` to `Active_Provider`.
- **Rotation_Strategy**: The algorithm used to select the next `Active_Provider`. Supported
  strategies are `priority` (always use the first non-exhausted provider) and `round-robin`
  (cycle through available providers in order).
- **Provider_State**: Runtime state associated with a provider: one of `available`, `rate_limited`
  (temporarily unavailable with a known retry time), or `quota_exhausted` (unavailable until daily
  reset).
- **Config_File**: `config.yaml` in the project root, extended with an `llm_providers` section.
- **Event_Classifier**: The existing classifier component in `app/pipeline/classifier.py`. Its
  public `classify()` method signature MUST NOT change.

---

## Requirements

### Requirement 1: Provider Configuration

**User Story:** As a developer, I want to configure multiple LLM providers in `config.yaml`, so
that the system has a pool of providers to draw from without modifying code or environment
variables per-provider.

#### Acceptance Criteria

1. THE `Config_File` SHALL support an `llm_providers` list where each entry contains the fields
   `name`, `base_url`, `api_key`, `model`, and an optional `weight` (positive integer, default 1).
2. WHEN the `api_key` value for a provider entry begins with `$`, THE `LLM_Provider_Manager` SHALL
   resolve it as an environment variable name, reading the key value from the environment at
   startup. IF the referenced environment variable is absent or empty, THEN THE
   `LLM_Provider_Manager` SHALL raise a `ConfigurationError` and abort startup, identifying the
   provider name and the missing variable.
3. IF the `llm_providers` list is absent or empty, THEN THE `LLM_Provider_Manager` SHALL fall back
   to the existing single-provider behaviour using the `LLM_BASE_URL`, `LLM_API_KEY`, and
   `LLM_MODEL` environment variables, preserving full backward compatibility. IF those environment
   variables are also absent, THEN THE `LLM_Provider_Manager` SHALL raise a `ConfigurationError`
   to the caller at request time, ensuring no classification request is partially processed.
4. THE `Config_File` SHALL support a `llm_rotation_strategy` field accepting the values `priority`
   or `round-robin` (default: `priority`).
5. IF a provider entry is missing `name`, `base_url`, or `model`, THEN THE `LLM_Provider_Manager`
   SHALL raise a `ConfigurationError` at startup with a message identifying the missing field and
   the 1-based index of the offending provider entry.

---

### Requirement 2: Provider State Tracking

**User Story:** As a developer, I want the system to track the health state of each provider at
runtime, so that exhausted or throttled providers are skipped automatically.

#### Acceptance Criteria

1. THE `LLM_Provider_Manager` SHALL maintain a `Provider_State` for each configured provider,
   initialised to `available` at startup.
2. WHEN a `Quota_Error` is received from a provider, THE `LLM_Provider_Manager` SHALL transition
   that provider's state to `quota_exhausted`.
3. WHEN a `Rate_Limit_Error` is received from a provider, IF a positive integer retry-after
   duration (≥ 1 second) is parseable from the error message, THEN THE `LLM_Provider_Manager`
   SHALL transition that provider's state to `rate_limited` and record the earliest datetime at
   which the provider may be retried, capped at 24 hours from the current time.
4. WHEN the retry-after datetime of a `rate_limited` provider has passed, THE
   `LLM_Provider_Manager` SHALL transition that provider's state back to `available` before the
   next provider selection.
5. IF all providers are in `quota_exhausted` or `rate_limited` state, THEN THE
   `LLM_Provider_Manager` SHALL return a `ClassificationResult` with `status="failed"` and a
   `details` entry `{"reason": "all_providers_exhausted"}` without making any LLM API call.
6. IF a `Rate_Limit_Error` is received from a provider and no positive integer retry-after duration
   is parseable from the error message, THEN THE `LLM_Provider_Manager` SHALL treat that error as
   a `Quota_Error` and transition the provider's state to `quota_exhausted`.

---

### Requirement 3: Fallback and Rotation

**User Story:** As a developer, I want the system to automatically fall back to the next provider
when the current one fails, so that classification continues without manual intervention.

#### Acceptance Criteria

1. WHEN the `Active_Provider` returns a `Quota_Error` or a `Rate_Limit_Error` with no remaining
   retry attempts, THE `LLM_Provider_Manager` SHALL immediately select the next `available`
   provider according to the configured `Rotation_Strategy` and retry the same classification
   request without surfacing the error to the caller.
2. IF the `Rotation_Strategy` is `priority`, THEN THE `LLM_Provider_Manager` SHALL always select
   the lowest-index `available` provider as the `Active_Provider`. WHEN the current
   `Active_Provider` transitions to `quota_exhausted`, THE pointer SHALL remain at the lowest
   available index rather than advancing; it advances only when a provider is skipped because it
   is non-`available`.
3. IF the `Rotation_Strategy` is `round-robin`, WHEN a classification request completes
   successfully, THEN THE `LLM_Provider_Manager` SHALL advance the `Active_Provider` pointer to
   the next `available` provider in circular order.
4. WHEN a fallback occurs, THE `LLM_Provider_Manager` SHALL complete the in-flight classification
   request using the new `Active_Provider` within the same call, returning a result to the caller.
5. THE `LLM_Provider_Manager` SHALL NOT retry a `quota_exhausted` provider for the remainder of
   the process lifetime (i.e., until the application restarts or an explicit reset is called).
6. FOR THE purposes of provider selection, an `available` provider is one whose `Provider_State`
   is not `quota_exhausted` and not `rate_limited` with a retry-after datetime in the future.
7. IF no `available` provider exists when a classification request is attempted, THEN THE
   `LLM_Provider_Manager` SHALL return a `ClassificationResult` with `status="failed"` and
   `details={"reason": "all_providers_exhausted"}` without making any LLM API call.

---

### Requirement 4: Observability and Logging

**User Story:** As a developer, I want clear log output showing which provider is active and when
fallback occurs, so that I can diagnose quota issues without inspecting raw API errors.

#### Acceptance Criteria

1. WHEN a classification request begins, THE `LLM_Provider_Manager` SHALL emit a DEBUG-level log
   entry containing the `Active_Provider` name and the model identifier string configured for that
   provider.
2. WHEN a fallback occurs, THE `LLM_Provider_Manager` SHALL emit a WARNING-level log entry
   containing the name of the demoted provider, the reason for demotion (`quota_exhausted` or
   `rate_limited`), and the name of the new `Active_Provider`.
3. IF all providers become unavailable, THEN THE `LLM_Provider_Manager` SHALL emit an ERROR-level
   log entry stating that all providers are exhausted and that classification cannot proceed.
4. WHEN a `rate_limited` provider transitions back to `available`, THE `LLM_Provider_Manager` SHALL
   emit an INFO-level log entry indicating the provider name and that it has been re-enabled. WHEN
   a `quota_exhausted` provider is explicitly reset to `available` via `reset_provider` or
   `reset_all_providers`, THE `LLM_Provider_Manager` SHALL also emit an INFO-level log entry
   indicating the provider name and that it has been re-enabled.
5. THE `LLM_Provider_Manager` SHALL include the provider `name` (not the raw `api_key`) in all log
   entries to avoid accidental credential exposure.

---

### Requirement 5: Event_Classifier Interface Preservation

**User Story:** As a developer, I want the `Event_Classifier.classify()` method signature to remain
unchanged, so that no call sites in the pipeline require modification.

#### Acceptance Criteria

1. THE `Event_Classifier` SHALL accept an optional `LLM_Provider_Manager` instance via constructor
   injection.
2. WHEN no `LLM_Provider_Manager` is provided to the `Event_Classifier` constructor, THE
   `Event_Classifier` SHALL instantiate a default one using the existing `LLM_BASE_URL`,
   `LLM_API_KEY`, and `LLM_MODEL` environment variables.
3. THE `Event_Classifier.classify()` method SHALL retain its existing signature:
   `async def classify(self, news_text: str, section_name: str, stock_name: str) -> ClassificationResult`.
4. WHEN the `LLM_Provider_Manager` signals that all providers are exhausted, THE `Event_Classifier`
   SHALL return a `ClassificationResult` with `status="failed"`, all typed fields set to `None`,
   `details={}`, and `tags=[]`.
5. THE `ClassificationResult` dataclass SHALL NOT have fields added or removed.

---

### Requirement 6: Configuration Round-Trip Integrity

**User Story:** As a developer, I want the provider configuration to be parsed and serialised
consistently, so that loading a saved config always produces an equivalent provider list.

#### Acceptance Criteria

1. THE `LLM_Provider_Manager` SHALL expose a method `get_provider_configs() -> list[ProviderConfig]`
   that returns the parsed provider list in the order they were defined in `config.yaml`.
2. THE `LLM_Provider_Manager` SHALL guarantee that for any valid `llm_providers` input (a non-empty
   list where each entry has a non-empty `name`), parsing then serialising then parsing that input
   SHALL produce a provider list with the same count, order, and field values as the original.
3. THE `LLM_Provider_Manager` SHALL treat provider `name` values as case-sensitive identifiers;
   two providers with names differing only in case SHALL be accepted as distinct providers.
4. IF the `llm_providers` list contains two entries with identical `name` values (same case), THEN
   THE `LLM_Provider_Manager` SHALL raise a `ConfigurationError` at startup identifying the
   duplicate name and the 1-based indices of the conflicting entries.

---

### Requirement 7: Quota Reset

**User Story:** As a developer, I want to be able to reset provider states at runtime, so that I
can recover from quota exhaustion without restarting the application.

#### Acceptance Criteria

1. WHEN `reset_provider(name: str)` is called, THE `LLM_Provider_Manager` SHALL transition the
   named provider's state to `available` and clear any stored retry-after timer, regardless of
   the provider's prior state.
2. WHEN `reset_all_providers()` is called, THE `LLM_Provider_Manager` SHALL transition all
   providers' states to `available` and clear all stored retry-after timers.
3. IF `reset_provider` is called with a `name` that does not match any configured provider, THEN
   THE `LLM_Provider_Manager` SHALL raise a `ValueError` identifying the unknown provider name.
4. WHEN `reset_provider` or `reset_all_providers` is called on a provider already in `available`
   state, THE `LLM_Provider_Manager` SHALL complete the operation without error (idempotent).
