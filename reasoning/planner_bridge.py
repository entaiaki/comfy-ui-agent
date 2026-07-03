#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert strategy steps to semantic ops when safe."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .models import StrategyStep

Json = Dict[str, Any]


def strategies_to_semantic_ops(strategy: Iterable[StrategyStep]) -> List[Json]:
    ops: List[Json] = []
    for step in strategy:
        if not step.target or step.value is None:
            continue
        if step.action in {"increase", "decrease", "slightly_increase", "slightly_decrease"}:
            ops.append({"op": "set", "target": step.target, "value": step.value})
        elif step.action == "append_style_tokens":
            # Current workflow_ops only guarantees set. Keep an explicit high-level op
            # for future prompt-aware planner; do not silently overwrite text here.
            ops.append({"op": "append_text", "target": step.target, "text": step.value, "separator": ", "})
        elif step.action == "append_negative_tokens":
            ops.append({"op": "append_text", "target": step.target, "text": step.value, "separator": ", "})
    return ops


def executable_ops_only(ops: Iterable[Json]) -> List[Json]:
    """Return ops that current workflow_ops is likely to execute directly."""
    return [dict(op) for op in ops if isinstance(op, dict) and op.get("op") == "set" and isinstance(op.get("target"), str)]
