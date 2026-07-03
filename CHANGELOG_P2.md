# CHANGELOG (P2) — Local GPT-Image Agent reliability & audit completion

Date: 2026-06-10

## Goal

Finish the production-style reliability layer for the Local GPT-Image Agent:

- Reliable task cancellation semantics
- Per-task hard timeout
- Retry policy with attempt tracking
- Stronger audit metadata
- Load test script and quantitative report template
- OpenAPI update for cancellation endpoint

## Added

### `patch_project_p2.py`

Applies P2 fixes to an existing `gpt_image_agent/` workspace:

- preserves user-provided `task_timeout_seconds`
- preserves user-provided `max_retries`
- preserves user-provided `retry_backoff_seconds`
- applies policy defaults only when user fields are missing
- injects `normalized_request.audit`
- makes worker skip tasks already marked `CANCELLED`
- updates `openapi.json` with `POST /task/{task_id}/cancel`

### `load_test.py`

A stdlib-only async load testing script for:

- `POST /submit`
- `GET /task/{task_id}`

It reports:

- total requests
- success count
- success rate
- avg latency
- p50 latency
- p95 latency
- p99 latency
- status breakdown
- raw task results

## Fixed

### Timeout / retry policy preservation

Previously, user-provided values such as:

```json
{
  "task_timeout_seconds": 1,
  "max_retries": 2,
  "retry_backoff_seconds": 0.5
}
```

could be lost during `normalize_request()`, causing the default policy value, such as `900`, to override the request.

P2 makes `normalize_request()` preserve these fields and applies defaults only when the request value is absent.

### Pending cancellation reliability

P2 keeps the existing cancellation registry and also ensures the worker skips tasks whose persisted status is already `CANCELLED`.

Cancellation semantics:

- `PENDING` task: reliably becomes `CANCELLED`
- `RUNNING` task: best-effort; cancellation request is recorded, but ComfyUI execution may already be in progress

### Audit fields

P2 adds best-effort audit metadata:

```json
{
  "bridge_version": "0.3-p2",
  "workflow_path": "...",
  "workflow_sha256": "...",
  "comfyui_server": "http://127.0.0.1:8186",
  "comfyui_version": "..."
}
```

## Verification checklist

### 1. Apply patch

```bash
cd gpt_image_agent
python patch_project_p2.py
```

### 2. Start bridge

```bash
python local_image_bridge_v03.py --host 127.0.0.1 --port 7861 --workers 2
```

### 3. Verify timeout preservation

```bash
curl -X POST http://127.0.0.1:7861/submit \
  -H "Content-Type: application/json" \
  -d '{"prompt":"timeout test","task_timeout_seconds":1,"max_retries":1,"retry_backoff_seconds":0.5}'
```

Then poll:

```bash
curl http://127.0.0.1:7861/task/<task_id>
```

Expected: `normalized_request.task_timeout_seconds` should be `1`, not `900`.

### 4. Verify cancellation

```bash
TASK_ID=$(curl -s -X POST http://127.0.0.1:7861/submit \
  -H "Content-Type: application/json" \
  -d '{"prompt":"cancel test"}' | python -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

curl -X POST http://127.0.0.1:7861/task/$TASK_ID/cancel
curl http://127.0.0.1:7861/task/$TASK_ID
```

Expected:

- If still pending: `CANCELLED`
- If already running: cancellation request recorded as best-effort

### 5. Verify audit fields

```bash
curl http://127.0.0.1:7861/task/<task_id>
```

Expected in `normalized_request.audit`:

- `bridge_version`
- `workflow_path`
- `workflow_sha256`
- `comfyui_server`
- `comfyui_version`

### 6. Run load test

```bash
python load_test.py --bridge http://127.0.0.1:7861 --requests 20 --concurrency 4 --width 512 --height 512 --steps 8
```

Expected output:

- terminal progress in console
- generated `LOAD_TEST_REPORT.md`

## Resume wording

> Built a local GPT image-generation agent platform integrating MCP tool calls, an HTTP Bridge service, asynchronous task queue, worker-based execution, workflow routing, API-key authentication, rate limiting, structured observability, reproducibility audit metadata, cancellation, timeout/retry policies, Docker/OpenAPI assets, and quantitative load testing.
