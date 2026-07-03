#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_task_store.py

A tiny task store for the local ComfyUI bridge.

Design goals:
- Standard library only.
- Persist tasks to a JSONL file for post-mortem and reproducibility.
- Thread-safe in-process usage.

Task lifecycle:
PENDING -> RUNNING -> SUCCEEDED | FAILED

Each task stores:
- request_id
- task_id
- normalized_request
- timestamps
- result (image paths etc.) or error

"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Optional


def _utc_ts() -> float:
    return time.time()


@dataclass
class Task:
    task_id: str
    request_id: str
    status: str = "PENDING"
    created_at: float = field(default_factory=_utc_ts)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    normalized_request: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


class TaskStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.data_dir / "tasks.jsonl"

        self._lock = threading.Lock()
        self._tasks: Dict[str, Task] = {}

    def new_task(self, request_id: str, normalized_request: Dict[str, Any]) -> Task:
        task = Task(task_id=str(uuid.uuid4()), request_id=request_id, normalized_request=normalized_request)
        with self._lock:
            self._tasks[task.task_id] = task
            self._append_event({"event": "created", **asdict(task)})
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **fields) -> Optional[Task]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            for k, v in fields.items():
                setattr(task, k, v)
            self._append_event({"event": "updated", **asdict(task)})
            return task

    def _append_event(self, obj: Dict[str, Any]):
        obj = dict(obj)
        obj["_ts"] = _utc_ts()
        line = json.dumps(obj, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
