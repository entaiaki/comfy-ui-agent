#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_decision.py

Compatibility wrapper for v11 Decision Engine.

Use this from bridge code:
    from workflow_decision import decide_workflow_action
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from reasoning.decision import DecisionPolicy, decide_from_reasoning, select_decisions
from workflow_reasoner import reason_about_workflow

Json = Dict[str, Any]


def decide_workflow_action(workflow: Mapping[str, Any], request: Mapping[str, Any]) -> Json:
    """Run Reasoner then Decision Engine.

    This function is side-effect free: no ComfyUI call, no workflow mutation.
    """

    reasoning = reason_about_workflow(workflow, request)
    decision = decide_from_reasoning(reasoning, request=request)
    return {
        "success": True,
        "request_id": decision.get("request_id") or reasoning.get("request_id"),
        "reasoning": reasoning,
        "decision": decision,
        "selected_ops": decision.get("selected_ops", []),
        "requires_review": decision.get("requires_review", True),
        "status": decision.get("status", "needs_review"),
    }


def decide(reasoning: Mapping[str, Any], request: Optional[Mapping[str, Any]] = None) -> Json:
    """Select a decision from an already computed reasoning result."""

    return decide_from_reasoning(reasoning, request=request)


__all__ = ["DecisionPolicy", "decide_workflow_action", "decide", "select_decisions"]
