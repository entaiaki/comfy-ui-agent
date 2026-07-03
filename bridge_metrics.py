#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_metrics.py

Simple in-process metrics store (thread-safe).

Avoids circular imports between worker and bridge.
"""

from __future__ import annotations

import threading
from typing import Dict


_lock = threading.Lock()
_metrics: Dict[str, int] = {
    "submit_total": 0,
    "sync_total": 0,
    "tasks_succeeded": 0,
    "tasks_failed": 0,
}


def inc(name: str, value: int = 1) -> None:
    with _lock:
        _metrics[name] = int(_metrics.get(name, 0)) + int(value)


def snapshot() -> Dict[str, int]:
    with _lock:
        return dict(_metrics)
