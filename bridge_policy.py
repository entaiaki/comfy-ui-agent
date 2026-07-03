#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_policy.py

Policy configuration for bridge tasks:
- per-task timeout
- retries
- backoff

Loaded from bridge_config.json if present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class Policy:
    task_timeout_seconds: int = 900
    max_retries: int = 0
    retry_backoff_seconds: float = 1.0


def load_policy(config_obj: Dict[str, Any]) -> Policy:
    p = config_obj.get("policy") or {}
    return Policy(
        task_timeout_seconds=int(p.get("task_timeout_seconds", 900)),
        max_retries=int(p.get("max_retries", 0)),
        retry_backoff_seconds=float(p.get("retry_backoff_seconds", 1.0)),
    )
