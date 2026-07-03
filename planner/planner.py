#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Planner Engine 2.0.

This module turns structured intent or reasoner strategy into ranked plan
candidates. It does not execute workflow ops.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .cost import combine_costs
from .exceptions import UnsupportedPlanningGoal
from .models import (
    Json,
    PlanAction,
    PlanCandidate,
    PlanPriority,
    PlanRequest,
    PlanResult,
    PlanStatus,
    clamp01,
    average,
)
from .optimizer import rank_candidates
from .strategy_adapter import actions_from_strategy, actions_from_structured_request, actions_from_text
from .validator import has_errors, validate_actions

try:
    from workflow_semantics import inspect_semantics
except Exception:  # pragma: no cover
    inspect_semantics = None


def _request_id(request: Mapping[str, Any]) -> str:
    value = request.get("request_id") or request.get("id")
    return str(value) if value else str(uuid.uuid4())


def _priority(value: Any) -> PlanPriority:
    text = str(value or "balanced").strip().lower().replace("-", "_")
    aliases = {
        "quality_first": "quality",
        "quality": "quality",
        "best_quality": "quality",
        "speed": "speed",
        "fast": "speed",
        "faster": "speed",
        "safe": "safety",
        "safety": "safety",
        "balanced": "balanced",
        "balance": "balanced",
    }
    return PlanPriority(aliases.get(text, "balanced"))


def normalize_plan_request(request: Mapping[str, Any]) -> PlanRequest:
    reasoner_result = request.get("reasoning") or request.get("reasoner_result") or request.get("reason") or {}
    if not isinstance(reasoner_result, Mapping):
        reasoner_result = {}
    text = str(request.get("text") or request.get("instruction") or request.get("message") or request.get("user_request") or "")
    goal = str(request.get("goal") or request.get("problem") or reasoner_result.get("problem", {}).get("normalized_goal", "") or request.get("intent") or "").strip()
    return PlanRequest(
        request_id=_request_id(request),
        goal=goal or "unknown",
        text=text,
        priority=_priority(request.get("priority") or request.get("mode")),
        intent=str(request.get("intent") or ""),
        parameters=dict(request),
        reasoner_result=dict(reasoner_result),
        max_candidates=max(1, min(int(request.get("max_candidates", 3)), 10)),
        allow_text_append_ops=bool(request.get("allow_text_append_ops", False)),
    )


def _semantics(workflow: Mapping[str, Any], supplied: Any = None) -> Json:
    if isinstance(supplied, Mapping):
        return dict(supplied)
    if inspect_semantics is None:
        return {"inferred_pipeline": "unknown", "main": {}, "counts": {}, "nodes": {}}
    try:
        return inspect_semantics(dict(workflow), include_nodes=True)
    except TypeError:
        return inspect_semantics(dict(workflow))


def _candidate_from_actions(candidate_id: str, title: str, title_zh: str, priority: PlanPriority, actions: Sequence[PlanAction], *, summary: str = "", summary_zh: str = "", tradeoffs: Sequence[str] = (), allow_text_append_ops: bool = False, trace: Sequence[Json] = ()) -> PlanCandidate:
    issues = validate_actions(actions, allow_text_append_ops=allow_text_append_ops)
    cost = combine_costs(actions)
    confidence = average([a.confidence for a in actions], default=0.0)
    if has_errors(issues):
        status = PlanStatus.UNSAFE
    elif cost.user_confirmation_cost > 0 or any(i.level == "warning" for i in issues):
        status = PlanStatus.NEEDS_CONFIRMATION
    else:
        status = PlanStatus.VALID
    return PlanCandidate(
        id=candidate_id,
        title=title,
        title_zh=title_zh,
        priority=priority,
        actions=tuple(actions),
        cost=cost,
        score=0.0,
        confidence=clamp01(confidence),
        status=status,
        summary=summary,
        summary_zh=summary_zh,
        tradeoffs=tuple(tradeoffs),
        issues=tuple(issues),
        trace=tuple(trace),
    )


class PlannerEngine:
    """Deterministic Planner Engine 2.0."""

    def plan(self, workflow: Mapping[str, Any], request: Mapping[str, Any]) -> PlanResult:
        normalized = normalize_plan_request(request)
        semantics = _semantics(workflow, request.get("semantics"))
        trace: List[Json] = [
            {"stage": "normalize", "message": "Normalized planner request.", "request": normalized.to_dict()},
            {"stage": "semantics", "message": "Loaded workflow semantics.", "pipeline": semantics.get("inferred_pipeline"), "main": semantics.get("main", {})},
        ]

        candidates: List[PlanCandidate] = []
        structured_actions = actions_from_structured_request(normalized.parameters)
        if structured_actions:
            candidates.append(_candidate_from_actions(
                "plan.intent.direct",
                "Direct intent plan",
                "直接意图规划",
                normalized.priority,
                structured_actions,
                summary="Apply the explicit user intent with minimal changes.",
                summary_zh="按用户明确意图做最小修改。",
                allow_text_append_ops=normalized.allow_text_append_ops,
                trace=trace,
            ))

        if normalized.text:
            text_actions = actions_from_text(normalized.text)
            if text_actions:
                candidates.append(_candidate_from_actions(
                    "plan.text.parsed",
                    "Parsed text plan",
                    "文本解析规划",
                    normalized.priority,
                    text_actions,
                    summary="Apply values parsed from the user text.",
                    summary_zh="应用从用户文本中解析出的参数。",
                    allow_text_append_ops=normalized.allow_text_append_ops,
                    trace=trace,
                ))

        reasoner_result = normalized.reasoner_result
        strategy = reasoner_result.get("strategy", []) if isinstance(reasoner_result, Mapping) else []
        if isinstance(strategy, list) and strategy:
            reason_actions = actions_from_strategy(strategy, semantics)
            if reason_actions:
                candidates.append(_candidate_from_actions(
                    "plan.reasoner.low_cost",
                    "Reasoner-guided low-cost plan",
                    "推理器引导的低成本规划",
                    normalized.priority,
                    reason_actions[:2],
                    summary="Use the safest high-confidence changes suggested by Reasoner.",
                    summary_zh="采用推理器建议中最安全、置信度较高的修改。",
                    tradeoffs=("Low-cost edits may not fully solve severe quality issues in one pass.",),
                    allow_text_append_ops=normalized.allow_text_append_ops,
                    trace=trace,
                ))
                if len(reason_actions) > 2:
                    candidates.append(_candidate_from_actions(
                        "plan.reasoner.quality",
                        "Reasoner-guided quality plan",
                        "推理器引导的质量优先规划",
                        PlanPriority.QUALITY,
                        reason_actions[:3],
                        summary="Apply more of the Reasoner strategy for stronger effect.",
                        summary_zh="应用更多推理器策略，追求更明显效果。",
                        tradeoffs=("May be slower or slightly riskier than the low-cost plan.",),
                        allow_text_append_ops=normalized.allow_text_append_ops,
                        trace=trace,
                    ))

        if not candidates:
            errors = ("No supported planning path. Provide intent fields, parseable text, or a Reasoner result.",)
            return PlanResult(normalized.request_id, False, None, (), errors=errors, trace=tuple(trace))

        ranked = rank_candidates(candidates, normalized.priority)
        ranked = ranked[: normalized.max_candidates]
        selected = next((c for c in ranked if c.status in {PlanStatus.VALID, PlanStatus.NEEDS_CONFIRMATION}), ranked[0] if ranked else None)
        success = bool(selected and selected.status != PlanStatus.UNSAFE)
        warnings: List[str] = []
        if selected and selected.status == PlanStatus.NEEDS_CONFIRMATION:
            warnings.append("Selected plan is usable but should be reviewed before automatic execution.")
        if selected and any(op.get("op") != "set" for op in selected.ops):
            warnings.append("Selected plan contains non-set ops; verify workflow_ops supports them before execution.")
        return PlanResult(
            request_id=normalized.request_id,
            success=success,
            selected=selected,
            candidates=tuple(ranked),
            warnings=tuple(warnings),
            trace=tuple(trace),
        )


def plan_workflow(workflow: Mapping[str, Any], request: Mapping[str, Any]) -> Json:
    return PlannerEngine().plan(workflow, request).to_dict()
