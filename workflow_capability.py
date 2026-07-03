#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
workflow_capability.py

Public facade for v15 Workflow Capability Registry.

This module is intentionally thin. It keeps HTTP bridge code from importing
internal capability package details and provides stable functions for future
Planner/Reasoner integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from capability.analyzer import summarize_manifest
from capability.cache import ManifestCache
from capability.manifest import build_manifest
from capability.matcher import match_capabilities
from capability.registry import default_registry
from capability.utils import stable_json_hash


def capability_registry_manifest() -> Dict[str, Any]:
    """Return known capability specs, not workflow-specific detection."""
    return default_registry().manifest()


def get_capability_manifest(
    workflow: Dict[str, Any],
    workflow_name: str = "",
    cache_dir: Optional[str | Path] = None,
    use_cache: bool = True,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Build or load a workflow capability manifest.

    Parameters:
        workflow: ComfyUI API/UI workflow JSON.
        workflow_name: optional display name/path.
        cache_dir: optional cache directory. If omitted, no file cache is used.
        use_cache: read/write cache when cache_dir is provided.
        refresh: force rebuild even if cache exists.
    """
    workflow_hash = stable_json_hash(workflow)
    cache: ManifestCache | None = ManifestCache(cache_dir) if cache_dir else None

    if cache and use_cache and not refresh:
        cached = cache.get(workflow_hash)
        if cached:
            cached["cache"] = {"hit": True, "workflow_hash": workflow_hash}
            return cached

    manifest = build_manifest(workflow, workflow_name=workflow_name).to_dict()
    manifest["cache"] = {"hit": False, "workflow_hash": workflow_hash}
    if cache and use_cache:
        path = cache.put(workflow_hash, manifest)
        manifest["cache"]["path"] = str(path)
    return manifest


def refresh_capability_manifest(
    workflow: Dict[str, Any],
    workflow_name: str = "",
    cache_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Force rebuild a workflow capability manifest."""
    return get_capability_manifest(workflow, workflow_name=workflow_name, cache_dir=cache_dir, use_cache=True, refresh=True)


def query_workflow_capabilities(
    workflow: Dict[str, Any],
    request: Dict[str, Any] | str,
    workflow_name: str = "",
    cache_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Query whether current workflow supports one or more capabilities."""
    if isinstance(request, str):
        query = request
        request_obj: Dict[str, Any] = {"query": request}
    else:
        request_obj = dict(request or {})
        query = str(request_obj.get("query") or request_obj.get("capability") or "")

    manifest = get_capability_manifest(
        workflow,
        workflow_name=workflow_name,
        cache_dir=cache_dir,
        use_cache=bool(request_obj.get("use_cache", True)),
        refresh=bool(request_obj.get("refresh", False)),
    )
    caps_by_name = {c.get("name"): c for c in manifest.get("capabilities", []) if isinstance(c, dict)}

    # Rehydrate minimal Capability-like objects via current build path for robust matching.
    # This avoids exposing internal dataclass serialization assumptions to the bridge.
    fresh = build_manifest(workflow, workflow_name=workflow_name)

    if "capabilities" in request_obj and isinstance(request_obj.get("capabilities"), list):
        results = []
        for item in request_obj.get("capabilities") or []:
            matched = match_capabilities(fresh.capabilities, str(item), manifest_hash=manifest.get("identity", {}).get("workflow_hash", ""))
            results.append(matched.to_dict())
        return {
            "success": True,
            "workflow": workflow_name,
            "manifest_summary": summarize_manifest(manifest),
            "results": results,
        }

    result = match_capabilities(fresh.capabilities, query, manifest_hash=manifest.get("identity", {}).get("workflow_hash", ""))
    return {
        "success": result.success,
        "workflow": workflow_name,
        "manifest_summary": summarize_manifest(manifest),
        "result": result.to_dict(),
        "capability": caps_by_name.get(query),
    }


def summarize_workflow_capabilities(
    workflow: Dict[str, Any],
    workflow_name: str = "",
    cache_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    manifest = get_capability_manifest(workflow, workflow_name=workflow_name, cache_dir=cache_dir)
    return summarize_manifest(manifest)
