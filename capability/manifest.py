#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capability manifest builder."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict

from .detector import detect_capabilities
from .graph import build_edges
from .models import CapabilityManifest
from .registry import CapabilityRegistry, default_registry


def build_manifest(workflow: Dict[str, Any], workflow_name: str = "", registry: CapabilityRegistry | None = None) -> CapabilityManifest:
    registry = registry or default_registry()
    identity, capabilities, providers, warnings = detect_capabilities(workflow, registry=registry)
    if workflow_name:
        identity = type(identity)(
            backend=identity.backend,
            pipeline=identity.pipeline,
            workflow_hash=identity.workflow_hash,
            workflow_name=workflow_name,
            node_count=identity.node_count,
            link_count=identity.link_count,
            generated_at=identity.generated_at,
        )
    edges = build_edges(capabilities)
    categories = Counter(c.category for c in capabilities)
    status = Counter(c.status for c in capabilities)
    statistics = {
        "capability_count": len(capabilities),
        "node_count": identity.node_count,
        "link_count": identity.link_count,
        "categories": dict(categories),
        "status": dict(status),
        "ready_count": sum(1 for c in capabilities if c.status in ("ready", "experimental")),
        "missing_dependency_count": sum(1 for c in capabilities if c.status == "missing_dependency"),
    }
    return CapabilityManifest(
        success=True,
        identity=identity,
        capabilities=capabilities,
        edges=edges,
        providers=providers,
        statistics=statistics,
        warnings=warnings,
    )
