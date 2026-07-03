# CHANGELOG (P0) — Local GPT-Image Agent bridge upgrade

Date: 2026-06-10

## Goal

Upgrade the **Local Image Bridge** from a sync-only script into a more "service-like" component:

- async task queue
- request_id + structured logging
- persisted task records for reproducibility

## What changed

### Added files

- `bridge_logging.py`
  - JSON structured logger to stdout (stdlib only)

- `bridge_task_store.py`
  - in-memory task store
  - persists task events to `bridge_data/tasks.jsonl` (JSONL)

- `bridge_worker.py`
  - background worker thread consuming task queue
  - updates TaskStore (PENDING -> RUNNING -> SUCCEEDED/FAILED)

### Updated `local_image_bridge_v03.py`

- Added global components:
  - `LOGGER`, `TASK_STORE`, `TASK_QUEUE`, `WORKER`

- New endpoints:
  - `POST /submit` (async): returns `task_id`
  - `GET /task/{task_id}`: query task status/result/error
  - `GET /metrics`: minimal queue size

- Kept backward-compatible endpoint:
  - `POST /generate-image` (sync)

- Adds `request_id`:
  - read from header `X-Request-Id` or auto-generated UUID

- Task reproducibility:
  - `/submit` normalizes request immediately and stores it
  - task lifecycle persisted to `bridge_data/tasks.jsonl`

## Verification

- ComfyUI reachable at `http://127.0.0.1:8186` (version observed: 0.22.3)
- `run_comfy_once.py` succeeded, produced:
  - `gpt_image_agent/generated_outputs/Flux_Kontext_API_Test_00001_.png`

- Bridge running at `http://127.0.0.1:7861`
- Async flow verified:
  - `POST /submit` -> task_id `877f679f-1501-403d-ba5e-75c02b7b9664`
  - `GET /task/{task_id}` -> SUCCEEDED
  - produced image:
    - `gpt_image_agent/generated_outputs/Flux_Bridge_00001_.png`
  - task events persisted:
    - `gpt_image_agent/bridge_data/tasks.jsonl`
