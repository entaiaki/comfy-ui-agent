#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_client.py

Client helpers for the local_image_bridge.

Used by MCP server to submit async jobs and poll results.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_json(method: str, url: str, payload=None, headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> dict:
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
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} error from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot connect to {url}\nReason: {e}") from e


def submit_and_wait(
    bridge_server: str,
    payload: Dict[str, Any],
    api_key: str = "",
    request_id: str = "",
    poll_interval: float = 1.0,
    timeout_seconds: int = 900,
) -> Dict[str, Any]:
    bridge_server = bridge_server.rstrip("/")
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if request_id:
        headers["X-Request-Id"] = request_id

    submit = _http_json("POST", bridge_server + "/submit", payload=payload, headers=headers, timeout=30)
    if not submit.get("success"):
        return submit

    task_id = submit.get("task_id")
    if not task_id:
        raise RuntimeError(f"Bridge submit succeeded but no task_id returned: {submit}")

    deadline = time.time() + int(timeout_seconds)
    while time.time() < deadline:
        st = _http_json("GET", bridge_server + f"/task/{task_id}", headers=headers, timeout=30)
        if not st.get("success"):
            return st
        task = st.get("task") or {}
        status = task.get("status")
        if status in ("SUCCEEDED", "FAILED"):
            # return the bridge task payload as-is (includes result/error)
            return st
        time.sleep(float(poll_interval))

    raise TimeoutError(f"Bridge task timeout after {timeout_seconds}s, task_id={task_id}")
