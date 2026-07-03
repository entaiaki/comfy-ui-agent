# LOAD_TEST_REPORT — Local GPT-Image Agent Bridge

## Test configuration

- Bridge: `http://127.0.0.1:7861`
- Requests: `6`
- Concurrency: `2`
- Size: `512x512`
- Steps: `6`
- Task timeout: `900s`
- Max retries: `0`

## Summary

- Total: `6`
- Succeeded: `6`
- Failed / timeout / cancelled: `0`
- Success rate: `100.00%`
- Avg latency: `43.573s`
- P50 latency: `42.216s`
- P95 latency: `57.376s`
- P99 latency: `57.376s`

## Status breakdown

- `SUCCEEDED`: `6`

## Raw results

```json
[
  {
    "index": 0,
    "task_id": "bc72ef9c-38d2-4e03-a490-5ba2b414e757",
    "ok": true,
    "status": "SUCCEEDED",
    "latency": 34.22352600097656,
    "error": null
  },
  {
    "index": 1,
    "task_id": "0addb2da-10e0-44b0-bfc5-199d65d07588",
    "ok": true,
    "status": "SUCCEEDED",
    "latency": 57.376380443573,
    "error": null
  },
  {
    "index": 2,
    "task_id": "946ae267-aec9-48c6-aec6-cccc91be1b06",
    "ok": true,
    "status": "SUCCEEDED",
    "latency": 42.21638298034668,
    "error": null
  },
  {
    "index": 3,
    "task_id": "e587ef1c-5455-496f-bd1b-2f1d29fc1a3f",
    "ok": true,
    "status": "SUCCEEDED",
    "latency": 41.19525194168091,
    "error": null
  },
  {
    "index": 4,
    "task_id": "102d8cb8-3bf9-499e-81e8-47476844a645",
    "ok": true,
    "status": "SUCCEEDED",
    "latency": 43.2186176776886,
    "error": null
  },
  {
    "index": 5,
    "task_id": "c6e4b866-8dbc-47a2-aace-eebadc940ed9",
    "ok": true,
    "status": "SUCCEEDED",
    "latency": 43.20709562301636,
    "error": null
  }
]
```
