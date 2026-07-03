# CHANGELOG (P1) — Local GPT-Image Agent "platformization"

Date: 2026-06-10

## Goal

Upgrade from a basic local bridge into a more complete, resume-worthy "service":

- API key auth
- per-key rate limiting
- multi-worker async execution
- workflow routing (profile -> workflow)
- MCP tool now routes through bridge async model (submit+poll)
- docker + openapi assets

## Changes

### Added

- `bridge_security.py`
  - `X-API-Key` auth (optional)
  - token bucket rate limiter (per API key)

- `bridge_workflow_router.py`
  - config-driven workflow resolution by `profile`

- `bridge_config.json`
  - api_key
  - rate_limit
  - workflow_profiles

- `bridge_metrics.py`
  - thread-safe in-process metrics store

- `bridge_client.py`
  - submit async job to bridge and poll until completion

- `Dockerfile.bridge`, `docker-compose.yml`
  - containerization assets for bridge (ComfyUI remains external by default)

- `openapi.json`
  - minimal OpenAPI spec for endpoints

### Updated

- `local_image_bridge_v03.py`
  - auth + rate limiting on all POST endpoints
  - `/submit` supports `profile` routing
  - `--workers N` starts N worker threads
  - `/metrics` now returns bridge_metrics snapshot

- `bridge_worker.py`
  - increments metrics via `bridge_metrics.inc` on success/failure

- `qwenpaw_flux_image_mcp.py`
  - `generate_flux_image` now uses bridge async model by default
  - new args: `bridge_server`, `bridge_api_key`, `profile`

## Verification

- Bridge running: `http://127.0.0.1:7861`
- Async submit+poll works with profile routing
- MCP tool call verified (Python direct call) produced image:
  - `gpt_image_agent/generated_outputs/MCP_Bridge_00001_.png`
- Metrics reflect successful tasks.
