#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File-based manifest cache for capability registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .exceptions import CapabilityCacheError


class ManifestCache:
    """Tiny deterministic cache keyed by workflow hash."""

    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, workflow_hash: str) -> Path:
        safe = "".join(ch for ch in workflow_hash if ch.isalnum() or ch in "-_")[:80]
        if not safe:
            raise CapabilityCacheError("workflow_hash is empty")
        return self.cache_dir / f"{safe}.capability_manifest.json"

    def get(self, workflow_hash: str) -> Optional[Dict[str, Any]]:
        path = self.path_for(workflow_hash)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            raise CapabilityCacheError(f"Failed to read capability cache {path}: {exc}") from exc

    def put(self, workflow_hash: str, manifest: Dict[str, Any]) -> Path:
        path = self.path_for(workflow_hash)
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            return path
        except Exception as exc:
            raise CapabilityCacheError(f"Failed to write capability cache {path}: {exc}") from exc
