#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_security.py

Very small auth + rate limit helpers (stdlib only).

- API key via header: X-API-Key
- Token bucket per api_key (in-memory)

This is meant for local usage and resume-worthy engineering signals.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class TokenBucket:
    capacity: float
    refill_rate_per_sec: float
    tokens: float
    last_refill: float


class ApiKeyAuth:
    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key

    def enabled(self) -> bool:
        return bool(self.api_key)

    def check(self, provided: Optional[str]) -> bool:
        if not self.api_key:
            return True
        return provided == self.api_key


class RateLimiter:
    def __init__(self, capacity: float = 10.0, refill_rate_per_sec: float = 1.0):
        self.capacity = float(capacity)
        self.refill_rate_per_sec = float(refill_rate_per_sec)
        self._lock = threading.Lock()
        self._buckets: Dict[str, TokenBucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> Tuple[bool, Dict[str, float]]:
        now = time.time()
        with self._lock:
            b = self._buckets.get(key)
            if not b:
                b = TokenBucket(
                    capacity=self.capacity,
                    refill_rate_per_sec=self.refill_rate_per_sec,
                    tokens=self.capacity,
                    last_refill=now,
                )
                self._buckets[key] = b

            # refill
            elapsed = now - b.last_refill
            if elapsed > 0:
                b.tokens = min(b.capacity, b.tokens + elapsed * b.refill_rate_per_sec)
                b.last_refill = now

            if b.tokens >= cost:
                b.tokens -= cost
                return True, {"tokens_left": b.tokens, "capacity": b.capacity}
            return False, {"tokens_left": b.tokens, "capacity": b.capacity}
