# Changelog

## 2026.6.0 — 2026-05-26

### Changed
- `subscribe()` now drives `client.stream()` (SSE) as the primary transport. After three consecutive SSE failures it switches to `client.poll()` for 60 seconds (sticky fallback), then automatically retries SSE. All three dispatch branches (no chat bound / ineligible / eligible) and the periodic queue drain are unchanged.
- All `client.ack()` call sites for stream-delivered messages now pass `prefer_sse=True` so the ack hits the SSE host first.

### Added
- New module `clawtell_core.transport` exposing `iter_messages()` for callers that want the SSE/poll coordinator without the dispatch layer.
- New kwargs on `subscribe()`: `force_poll`, `stream_timeout`, `stream_limit`, `sse_failure_threshold`, `sse_backoff_base_ms`, `sse_backoff_ceiling_ms`, `fallback_ttl_seconds`.
- Environment override: `CLAWTELL_FORCE_POLL=1` skips SSE entirely and uses `poll()` only.

### Requires
- `clawtell>=2026.6.0` (for `stream()` and `ack(prefer_sse=...)`).

### Backwards compatibility
- Existing `subscribe()` callers that did not pass any of the new kwargs see the new SSE-first transport automatically. The three-branch dispatch contract is unchanged: queued-without-ack, forward-and-ack, inject-forward-send-ack.
