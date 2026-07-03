#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_executor.py

Run a function with a hard timeout using multiprocessing.

We use this to enforce per-task timeouts even if ComfyUI call blocks.

Note: On Windows, multiprocessing needs the "spawn" method; this module is designed
for simple function+dict usage.
"""

from __future__ import annotations

import multiprocessing as mp
from typing import Any, Callable, Dict, Tuple


def _runner(fn: Callable[[Dict[str, Any]], Dict[str, Any]], payload: Dict[str, Any], q: "mp.Queue"):
    try:
        q.put((True, fn(payload)))
    except Exception as e:
        q.put((False, (type(e).__name__, str(e))))


def run_with_timeout(fn: Callable[[Dict[str, Any]], Dict[str, Any]], payload: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
    timeout_seconds = int(timeout_seconds)
    q: mp.Queue = mp.Queue()
    p = mp.Process(target=_runner, args=(fn, payload, q), daemon=True)
    p.start()
    p.join(timeout=timeout_seconds)

    if p.is_alive():
        p.terminate()
        p.join(timeout=2)
        raise TimeoutError(f"Task timed out after {timeout_seconds}s")

    ok, res = q.get() if not q.empty() else (False, ("Unknown", "No result returned"))
    if ok:
        return res
    etype, msg = res
    raise RuntimeError(f"{etype}: {msg}")
