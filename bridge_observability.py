#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_observability.py

Observability helpers:
- latency histogram (very small, coarse buckets)
- Prometheus text export

stdlib only.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Tuple


class LatencyHistogram:
    def __init__(self, buckets: List[float]):
        self.buckets = sorted(buckets)
        self._lock = threading.Lock()
        self.counts = [0 for _ in self.buckets]
        self.inf_count = 0
        self.sum = 0.0
        self.total = 0

    def observe(self, seconds: float):
        seconds = float(seconds)
        with self._lock:
            self.total += 1
            self.sum += seconds
            placed = False
            for i, b in enumerate(self.buckets):
                if seconds <= b:
                    self.counts[i] += 1
                    placed = True
                    break
            if not placed:
                self.inf_count += 1

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "buckets": list(self.buckets),
                "counts": list(self.counts),
                "inf_count": self.inf_count,
                "sum": self.sum,
                "total": self.total,
            }


_histograms = {
    "sync_latency": LatencyHistogram([0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600]),
    "task_latency": LatencyHistogram([0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600]),
}


def observe(name: str, seconds: float):
    h = _histograms.get(name)
    if h:
        h.observe(seconds)


def snapshot() -> Dict[str, object]:
    return {k: v.snapshot() for k, v in _histograms.items()}


def to_prometheus(metrics: Dict[str, int], histograms: Dict[str, object], extra: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append('# HELP bridge_requests_total Total requests by type')
    lines.append('# TYPE bridge_requests_total counter')
    lines.append(f'bridge_requests_total{{type="submit"}} {metrics.get("submit_total", 0)}')
    lines.append(f'bridge_requests_total{{type="sync"}} {metrics.get("sync_total", 0)}')

    lines.append('# HELP bridge_tasks_total Total tasks by status')
    lines.append('# TYPE bridge_tasks_total counter')
    lines.append(f'bridge_tasks_total{{status="succeeded"}} {metrics.get("tasks_succeeded", 0)}')
    lines.append(f'bridge_tasks_total{{status="failed"}} {metrics.get("tasks_failed", 0)}')

    qsize = extra.get('queue_size')
    if qsize is not None:
        lines.append('# HELP bridge_queue_size Current queue size')
        lines.append('# TYPE bridge_queue_size gauge')
        lines.append(f'bridge_queue_size {int(qsize)}')

    # Histograms
    for name, snap in histograms.items():
        buckets = snap["buckets"]
        counts = snap["counts"]
        inf = snap["inf_count"]
        total = snap["total"]
        ssum = snap["sum"]

        lines.append(f'# HELP bridge_{name}_seconds Latency histogram in seconds')
        lines.append(f'# TYPE bridge_{name}_seconds histogram')

        cumulative = 0
        for b, c in zip(buckets, counts):
            cumulative += c
            lines.append(f'bridge_{name}_seconds_bucket{{le="{b}"}} {cumulative}')
        lines.append(f'bridge_{name}_seconds_bucket{{le="+Inf"}} {cumulative + inf}')
        lines.append(f'bridge_{name}_seconds_sum {ssum}')
        lines.append(f'bridge_{name}_seconds_count {total}')

    return "\n".join(lines) + "\n"
