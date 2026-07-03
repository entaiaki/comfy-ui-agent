#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capability.models

Typed data models for the Workflow Capability Registry.

The registry answers a different question from workflow semantics:
- Semantics: what is this node?
- Capabilities: what can this workflow do?
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WorkflowIdentity:
    """Stable identity for a workflow snapshot."""

    backend: str
    pipeline: str
    workflow_hash: str
    workflow_name: str = ""
    node_count: int = 0
    link_count: int = 0
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityProvider:
    """A node/package/provider that proves a capability exists."""

    node_id: str
    class_type: str
    provider: str = "unknown"
    role: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Capability:
    """One detected workflow ability."""

    name: str
    category: str
    status: str = "ready"  # ready | partial | experimental | deprecated | missing_dependency
    confidence: float = 0.0
    priority: int = 50
    native: bool = False
    experimental: bool = False
    providers: List[CapabilityProvider] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    enables: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["providers"] = [p.to_dict() for p in self.providers]
        return data


@dataclass(frozen=True)
class CapabilityEdge:
    """Relationship between two capabilities."""

    source: str
    relation: str
    target: str
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityManifest:
    """The canonical output of the Capability Registry."""

    success: bool
    identity: WorkflowIdentity
    capabilities: List[Capability] = field(default_factory=list)
    edges: List[CapabilityEdge] = field(default_factory=list)
    providers: Dict[str, int] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    registry_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "registry_version": self.registry_version,
            "identity": self.identity.to_dict(),
            "capabilities": [c.to_dict() for c in self.capabilities],
            "capability_names": [c.name for c in self.capabilities],
            "edges": [e.to_dict() for e in self.edges],
            "providers": dict(self.providers),
            "statistics": dict(self.statistics),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CapabilityQueryResult:
    """Result returned by capability query/matcher."""

    success: bool
    query: str
    matches: List[Capability] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    manifest_hash: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "query": self.query,
            "matches": [m.to_dict() for m in self.matches],
            "missing": list(self.missing),
            "manifest_hash": self.manifest_hash,
            "message": self.message,
        }
