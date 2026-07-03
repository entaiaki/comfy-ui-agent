#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_cancel.py

Task cancellation registry (in-process).

- Allows marking a task_id as cancelled.
- Workers should check cancellation before starting expensive work.

stdlib only.
"""

from __future__ import annotations

import threading
from typing import Set


_lock = threading.Lock()
_cancelled: Set[str] = set()


def cancel(task_id: str) -> None:
    with _lock:
        _cancelled.add(str(task_id))


def is_cancelled(task_id: str) -> bool:
    with _lock:
        return str(task_id) in _cancelled
