#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capability relationship graph."""

from __future__ import annotations

from typing import Iterable, List, Set

from .models import Capability, CapabilityEdge


def build_edges(capabilities: Iterable[Capability]) -> List[CapabilityEdge]:
    names: Set[str] = {c.name for c in capabilities}
    edges: List[CapabilityEdge] = []
    for cap in capabilities:
        for dep in cap.depends_on:
            relation = "depends_on" if dep in names else "missing_dependency"
            confidence = 1.0 if dep in names else 0.5
            edges.append(CapabilityEdge(source=cap.name, relation=relation, target=dep, confidence=confidence))
        for enabled in cap.enables:
            edges.append(CapabilityEdge(source=cap.name, relation="enables", target=enabled, confidence=0.9))
    return edges
