#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_retry.py

Retry helper for tasks.

We store retry count inside normalized_request['__attempt'].
"""

from __future__ import annotations

import time
from typing import Dict, Any


def get_attempt(req: Dict[str, Any]) -> int:
    return int(req.get("__attempt", 1))


def bump_attempt(req: Dict[str, Any]) -> Dict[str, Any]:
    req = dict(req)
    req["__attempt"] = get_attempt(req) + 1
    return req


def backoff_sleep(seconds: float):
    time.sleep(float(seconds))
