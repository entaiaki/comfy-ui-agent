# Local GPT-Image Agent (Workspace Sync)

This folder is a synced copy of the **Local GPT-Image Agent** system for easier review and iteration.

## Comfy Ops variant

This variant adds **workflow graph editing** via an `ops` array in the request body.
See `workflow_ops.py`.

- Source (original): `E:\AI\ComfyUI-aki-v1.4\ComfyUI-aki-v1.4\my_workflows\`
- Synced workspace copy: `gpt_image_agent\`

## What this system does

- Provides a local HTTP bridge for ComfyUI image generation.
- Supports robust parsing of QwenPaw outputs (flat JSON, nested JSON, markdown fenced JSON, raw text containing JSON).
- Integrates with MCP tooling (see `qwenpaw_flux_image_mcp.py`).

## Key scripts

- `local_image_bridge_v03.py` (bridge HTTP server)
- `qwenpaw_flux_image_mcp.py` (MCP tool server)
- `run_comfy_once.py` (ComfyUI API smoke test)
- `flux_kontext_txt2img_api_workflow.json` (ComfyUI API workflow)

## New (2026-06-10): Async & observability improvements (P0)

The bridge now supports an **async task model**:

- `POST /submit` returns immediately with a `task_id`
- `GET /task/{task_id}` returns task status and result
- `GET /metrics` returns minimal queue metrics

Also adds:
- `request_id` (auto-generated or provided via `X-Request-Id` header)
- JSON structured logs to stdout
- task persistence as `bridge_data/tasks.jsonl` for reproducibility & post-mortem

## New: Auth / Rate limit / Workflow routing / Multi-worker

- Optional API key auth: set `api_key` in `bridge_config.json` or pass `--api-key`.
  - Client header: `X-API-Key: <key>`
- Token bucket rate limiting per API key (`429` when exceeded).
- Workflow routing:
  - Request may include `profile` (e.g. `default`) to select workflow via `bridge_config.json` -> `workflow_profiles`.
- Multi-worker:
  - `--workers N` starts N background worker threads.

## API spec

- `openapi.json` (basic OpenAPI 3.0 spec)

## Run bridge

```bat
cd /d C:\Users\Administrator\.qwenpaw\workspaces\default\gpt_image_agent
python local_image_bridge_v03.py --host 127.0.0.1 --port 7861 --comfyui-server http://127.0.0.1:8186 --workflow flux_kontext_txt2img_api_workflow.json --out-dir generated_outputs
```

## Test async flow

Submit:

```bat
curl -X POST http://127.0.0.1:7861/submit -H "Content-Type: application/json" -d "{\"prompt\":\"a cute cat\",\"width\":1024,\"height\":1280}" 
```

Poll:

```bat
curl http://127.0.0.1:7861/task/<task_id>
```

## Notes

- ComfyUI must be running and reachable at the configured URL.
- For now, the async worker count is fixed to 1 in code, but CLI flags `--workers` are reserved for scaling next.
