#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory.store

Append-only JSONL store for Agent Memory.

Why JSONL / 为什么用 JSONL：
- simple to inspect by humans；人工可读。
- append-only and low risk；追加写入，风险低。
- easy to migrate to SQLite/vector DB later；以后迁移到 SQLite/向量库容易。

This store is intentionally conservative. It performs atomic-ish line appends and
skips corrupted lines while reporting them in stats.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .models import ExperienceEntry, Json


class MemoryStoreError(ValueError):
    """Raised when memory storage cannot be read or written safely."""


class JsonlMemoryStore:
    """Small durable append-only memory store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: ExperienceEntry) -> ExperienceEntry:
        self.ensure_parent()
        line = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # Some filesystems or containers may not support fsync well.
                    pass
        return entry

    def load_all(self, *, max_entries: int = 5000) -> Tuple[List[ExperienceEntry], int]:
        """Return entries and corrupted-line count.

        ``max_entries`` keeps the bridge responsive if the file grows large.
        The newest entries are usually most useful, so this method keeps the tail
        when max_entries is exceeded.
        """
        if not self.path.exists():
            return [], 0
        entries: List[ExperienceEntry] = []
        bad_lines = 0
        with self.path.open("r", encoding="utf-8-sig") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    if isinstance(data, Mapping):
                        entries.append(ExperienceEntry.from_mapping(data))
                    else:
                        bad_lines += 1
                except Exception:
                    bad_lines += 1
        if len(entries) > max_entries:
            entries = entries[-max_entries:]
        return entries, bad_lines

    def stats(self) -> Json:
        entries, bad_lines = self.load_all(max_entries=100000)
        by_kind: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        by_goal: Dict[str, int] = {}
        for item in entries:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            by_status[item.outcome.status] = by_status.get(item.outcome.status, 0) + 1
            if item.goal:
                by_goal[item.goal] = by_goal.get(item.goal, 0) + 1
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "entries": len(entries),
            "bad_lines": bad_lines,
            "by_kind": by_kind,
            "by_outcome_status": by_status,
            "by_goal": by_goal,
        }
