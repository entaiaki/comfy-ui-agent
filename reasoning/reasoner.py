#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reasoner core for the ComfyUI Agent framework.

This module coordinates deterministic reasoning:
request -> problem -> observations -> evidence -> hypotheses -> scoring -> strategy.
It never mutates workflows and never calls ComfyUI.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Mapping, Optional

from .diagnosis import normalize_problem
from .evidence import collect_evidence
from .hypothesis import generate_default_hypotheses
from .models import ReasoningResult, confidence_band, average, clamp01
from .observer import observe_workflow, observations_to_map
from .planner_bridge import executable_ops_only, strategies_to_semantic_ops
from .scoring import score_hypotheses
from .strategy import build_strategy
from .trace import TraceBuilder

try:
    from workflow_semantics import inspect_semantics, summarize_semantics
except Exception:  # pragma: no cover
    inspect_semantics = None
    summarize_semantics = None

Json = Dict[str, Any]


class WorkflowReasoner:
    """Deterministic workflow reasoning engine."""

    def __init__(self, *, max_strategy_steps: int = 3, min_auto_confidence: float = 0.70) -> None:
        self.max_strategy_steps = max(1, int(max_strategy_steps))
        self.min_auto_confidence = clamp01(min_auto_confidence)

    def _request_id(self, request: Mapping[str, Any]) -> str:
        rid = request.get("request_id") or request.get("id")
        return str(rid) if rid else str(uuid.uuid4())

    def _semantics(self, workflow: Mapping[str, Any], request: Mapping[str, Any]) -> Json:
        supplied = request.get("semantics")
        if isinstance(supplied, Mapping):
            return dict(supplied)
        if inspect_semantics is None:
            return {"inferred_pipeline": "unknown", "counts": {}, "main": {}, "roles": {}, "warnings": ["workflow_semantics unavailable"]}
        return inspect_semantics(dict(workflow), include_nodes=True)

    def reason(self, workflow: Mapping[str, Any], request: Mapping[str, Any]) -> ReasoningResult:
        if not isinstance(workflow, Mapping):
            raise TypeError("workflow must be a mapping")
        if not isinstance(request, Mapping):
            raise TypeError("request must be a mapping")

        trace = TraceBuilder()
        request_id = self._request_id(request)
        problem = normalize_problem(request)
        trace.add("diagnosis", "Normalized user problem.", "已规范化用户目标。", problem=problem.to_dict())

        semantics = self._semantics(workflow, request)
        trace.add(
            "semantics",
            "Loaded semantic workflow context.",
            "已读取工作流语义上下文。",
            inferred_pipeline=semantics.get("inferred_pipeline"),
            counts=semantics.get("counts", {}),
            main=semantics.get("main", {}),
        )

        observations = observe_workflow(semantics)
        obs_map = observations_to_map(observations)
        trace.add("observe", "Collected deterministic workflow observations.", "已收集确定性的工作流观察。", observation_count=len(observations))

        evidence = collect_evidence(problem, observations)
        trace.add("evidence", "Collected knowledge and workflow evidence.", "已收集知识证据和工作流证据。", evidence_count=len(evidence))

        hypotheses = generate_default_hypotheses(problem, obs_map)
        trace.add("hypothesis", "Generated candidate hypotheses.", "已生成候选假设。", hypothesis_count=len(hypotheses))

        scored = score_hypotheses(hypotheses, obs_map, evidence)
        trace.add("scoring", "Scored hypotheses by confidence, cost, and risk.", "已按置信度、成本和风险为假设打分。", top_score=scored[0].score if scored else 0)

        strategy = build_strategy(scored, obs_map, max_steps=self.max_strategy_steps)
        semantic_ops = strategies_to_semantic_ops(strategy)
        executable_ops = executable_ops_only(semantic_ops)
        trace.add("strategy", "Built strategy steps and candidate semantic ops.", "已生成策略步骤和候选语义操作。", strategy_count=len(strategy), suggested_ops=len(semantic_ops), executable_ops=len(executable_ops))

        confidence = average([item.score for item in scored[:2]], default=problem.confidence)
        if problem.normalized_goal == "unknown":
            confidence = min(confidence, 0.35)
        warnings = []
        ask_before = []
        if not executable_ops:
            ask_before.append("No directly executable low-level set operation was produced; planner/user confirmation is required.")
        if any(item.risk >= 0.65 for item in scored[:1]):
            warnings.append("Top hypothesis has high risk; avoid automatic apply.")
        if float(obs_map.get("count.unknown_nodes") or 0) > 0:
            warnings.append("Workflow contains unknown/custom nodes; prefer dry-run and human review before execution.")

        safe_to_auto_plan = bool(executable_ops) and confidence >= self.min_auto_confidence and not warnings
        return ReasoningResult(
            request_id=request_id,
            problem=problem,
            observations=tuple(observations),
            evidence=tuple(evidence),
            hypotheses=tuple(scored),
            strategy=tuple(strategy),
            suggested_ops=tuple(semantic_ops),
            confidence=clamp01(confidence),
            confidence_band=confidence_band(confidence),
            safe_to_auto_plan=safe_to_auto_plan,
            ask_before=tuple(ask_before),
            warnings=tuple(warnings),
            trace=tuple(trace.build()),
        )


def reason_about_workflow(workflow: Mapping[str, Any], request: Mapping[str, Any]) -> Json:
    """Convenience function for bridge endpoints."""
    return WorkflowReasoner().reason(workflow, request).to_dict()
