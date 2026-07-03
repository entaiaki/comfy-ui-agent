#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human-readable analysis helpers for capability manifests."""

from __future__ import annotations

from typing import Any, Dict


def summarize_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    caps = manifest.get("capabilities") or []
    ready = [c.get("name") for c in caps if c.get("status") in ("ready", "experimental")]
    missing = [c.get("name") for c in caps if c.get("status") == "missing_dependency"]
    identity = manifest.get("identity") or {}
    return {
        "success": bool(manifest.get("success")),
        "backend": identity.get("backend"),
        "pipeline": identity.get("pipeline"),
        "workflow_hash": identity.get("workflow_hash"),
        "node_count": identity.get("node_count"),
        "ready_capabilities": ready,
        "missing_dependency_capabilities": missing,
        "capability_count": len(caps),
    }
