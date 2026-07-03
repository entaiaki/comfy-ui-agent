# ROADMAP — Local GPT-Image Agent (Bridge + MCP)

This file tracks the planned upgrade path and current progress.

## Mainline positioning
AI Agent engineering delivery + backend engineering signals.

## P0 (done)
- Async task model: /submit + /task/{id}
- request_id + structured logs
- persisted task events (JSONL)

## P1 (done)
- API key auth (optional)
- token bucket rate limiting
- workflow routing by profile (config-driven)
- multi-worker background execution
- MCP tool routes through bridge async model
- docker + openapi assets

## P2 (next)
- Prometheus-style text metrics (and richer metrics)
- task cancellation
- per-task timeout + retry policy
- stronger reproducibility/audit fields (workflow hash, comfyui version, bridge version)
- load test script + report (avg/p95/p99, success rate)
