#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
load_test.py

Small stdlib-only load test for Local GPT-Image Agent Bridge.
It tests the async endpoint:

    POST /submit
    GET  /task/{task_id}

Example:

    python load_test.py --bridge http://127.0.0.1:7861 --requests 20 --concurrency 4

Optional API key:

    python load_test.py --api-key YOUR_KEY
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def http_json(method: str, url: str, payload: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None, timeout: int = 30) -> Dict[str, Any]:
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    req = Request(url, data=data, headers=hdrs, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"success": False, "http_status": exc.code, "error": body}
    except URLError as exc:
        return {"success": False, "error": str(exc)}


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return float(ordered[idx])


def run_one(index: int, args: argparse.Namespace) -> Dict[str, Any]:
    bridge = args.bridge.rstrip("/")
    headers = {"X-Request-Id": str(uuid.uuid4())}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    payload = {
        "prompt": f"load test image {index}, clean product photo, simple background",
        "negative": "watermark, text, blurry, low quality",
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "filename_prefix": f"LoadTest_{index}",
        "profile": args.profile,
        "task_timeout_seconds": args.task_timeout,
        "max_retries": args.max_retries,
        "retry_backoff_seconds": args.retry_backoff,
    }

    started = time.time()
    submit = http_json("POST", bridge + "/submit", payload=payload, headers=headers, timeout=30)
    if not submit.get("success"):
        return {
            "index": index,
            "ok": False,
            "status": "SUBMIT_FAILED",
            "latency": time.time() - started,
            "error": submit.get("error") or submit,
        }

    task_id = submit.get("task_id")
    deadline = time.time() + args.wait_timeout

    last = None
    while time.time() < deadline:
        last = http_json("GET", bridge + f"/task/{task_id}", headers=headers, timeout=30)
        task = last.get("task") or {}
        status = task.get("status")
        if status in TERMINAL:
            latency = time.time() - started
            return {
                "index": index,
                "task_id": task_id,
                "ok": status == "SUCCEEDED",
                "status": status,
                "latency": latency,
                "error": task.get("error"),
            }
        time.sleep(args.poll_interval)

    return {
        "index": index,
        "task_id": task_id,
        "ok": False,
        "status": "CLIENT_TIMEOUT",
        "latency": time.time() - started,
        "error": last,
    }


def make_report(results: List[Dict[str, Any]], output: Path, args: argparse.Namespace) -> None:
    latencies = [float(r["latency"]) for r in results]
    ok_count = sum(1 for r in results if r.get("ok"))
    total = len(results)
    failed = total - ok_count
    avg = statistics.mean(latencies) if latencies else 0.0
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    p99 = percentile(latencies, 0.99)

    by_status: Dict[str, int] = {}
    for r in results:
        status = str(r.get("status"))
        by_status[status] = by_status.get(status, 0) + 1

    lines = []
    lines.append("# LOAD_TEST_REPORT — Local GPT-Image Agent Bridge")
    lines.append("")
    lines.append("## Test configuration")
    lines.append("")
    lines.append(f"- Bridge: `{args.bridge}`")
    lines.append(f"- Requests: `{args.requests}`")
    lines.append(f"- Concurrency: `{args.concurrency}`")
    lines.append(f"- Size: `{args.width}x{args.height}`")
    lines.append(f"- Steps: `{args.steps}`")
    lines.append(f"- Task timeout: `{args.task_timeout}s`")
    lines.append(f"- Max retries: `{args.max_retries}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total: `{total}`")
    lines.append(f"- Succeeded: `{ok_count}`")
    lines.append(f"- Failed / timeout / cancelled: `{failed}`")
    lines.append(f"- Success rate: `{(ok_count / total * 100) if total else 0:.2f}%`")
    lines.append(f"- Avg latency: `{avg:.3f}s`")
    lines.append(f"- P50 latency: `{p50:.3f}s`")
    lines.append(f"- P95 latency: `{p95:.3f}s`")
    lines.append(f"- P99 latency: `{p99:.3f}s`")
    lines.append("")
    lines.append("## Status breakdown")
    lines.append("")
    for status, count in sorted(by_status.items()):
        lines.append(f"- `{status}`: `{count}`")
    lines.append("")
    lines.append("## Raw results")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(results, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Async load test for Local GPT-Image Agent Bridge")
    parser.add_argument("--bridge", default="http://127.0.0.1:7861")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--task-timeout", type=int, default=900)
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--retry-backoff", type=float, default=1.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--wait-timeout", type=int, default=1800)
    parser.add_argument("--output", default="LOAD_TEST_REPORT.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results: List[Dict[str, Any]] = []

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run_one, i, args) for i in range(args.requests)]
        for future in as_completed(futures):
            item = future.result()
            results.append(item)
            print(f"[{len(results)}/{args.requests}] index={item.get('index')} status={item.get('status')} latency={item.get('latency'):.3f}s")

    elapsed = time.time() - started
    results.sort(key=lambda x: int(x.get("index", 0)))
    output = Path(args.output)
    make_report(results, output, args)
    print(f"\n[DONE] elapsed={elapsed:.3f}s report={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
