# Changelog

All notable changes to the AgntSpark Python SDK are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.2.0] — 2026-01-15

### Added
- SSE streaming for real-time metrics via `client.agents.stream_metrics()`
- Token-bucket rate limiter with configurable `rate_limit_rpm`
- `ResourceLimits.ephemeral_storage_gb` field for scratch storage quotas
- Jitter in retry backoff to prevent thundering-herd effects

### Changed
- `AgentStatus` now exposes `is_healthy` property for quick status checks
- Improved docstrings across all public methods

### Fixed
- `DeployConfig` validation now correctly requires `gpu_type` when `gpu > 0`
- `max_replicas` validation ensures it's never less than `min_replicas`

## [1.1.0] — 2025-12-01

### Added
- Async variants (`_async`) for every agent method
- `stream_logs()` SSE endpoint for real-time log tailing
- CLI tool: `agntspark deploy`, `list`, `logs`, `init`, `metrics`
- Config file support at `~/.agntspark/config.yaml`
- Environment variable configuration (`AGNTSPARK_*`)

### Changed
- Migrated from Pydantic v1 to Pydantic v2
- `httpx` replaced `requests` for both sync and async HTTP

### Fixed
- Retry logic no longer retries on 4xx (except 429)
- Proper `Retry-After` header parsing for 429 responses

## [1.0.0] — 2025-10-15

### Added
- Initial release
- `Client` class with synchronous HTTP support
- `Agents` namespace with: `create`, `deploy`, `list`, `get`, `delete`, `logs`, `metrics`, `scale`
- Pydantic models: `AgentConfig`, `AgentResponse`, `DeployConfig`, `Metrics`, `AgentLog`, `ResourceLimits`
- Exception hierarchy: `AgntSparkError`, `AuthenticationError`, `RateLimitError`, `NotFoundError`, `DeploymentError`
- Auto-retry with exponential backoff for transient HTTP errors
- MIT license
