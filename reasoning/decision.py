#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reasoning.decision

Decision Engine for the ComfyUI Agent reasoning layer.

English -> 中文对应：
- Decision Engine -> 决策引擎：从 Reasoner 给出的多个候选策略里选择当前最值得执行的一步。
- Decision -> 决策：一个可解释、可审查、可交给 Planner / Workflow Ops 的选择。
- Decision Policy -> 决策策略：选择时遵守的阈值、风险、成本和自动执行边界。
- Guardrail -> 护栏：防止低置信、高风险、图结构级修改被自动执行的安全规则。

Design boundary / 设计边界：
This module is deterministic and side-effect free. It does not call ComfyUI,
does not write workflow files, and does not mutate workflow JSON. It consumes
ReasoningResult-like data and returns a DecisionResult.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import clamp01

Json = Dict[str, Any]


class DecisionStatus(str, Enum):
    """Final decision status."""

    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    NEEDS_CLARIFICATION = "needs_clarification"
    REJECTED = "rejected"


class DecisionKind(str, Enum):
    """Type of selected decision."""

    EXECUTABLE_OP = "executable_op"
    PLANNER_INTENT = "planner_intent"
    ADVICE_ONLY = "advice_only"
    CLARIFICATION = "clarification"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class DecisionPolicy:
    """Policy controlling how aggressively a decision may be accepted.

    min_confidence: minimum per-step confidence.
    min_score: minimum rank score produced by this engine.
    max_risk: maximum numeric risk allowed for automatic acceptance.
    max_cost: maximum numeric cost allowed for automatic acceptance.
    allow_non_executable: whether advice-only / future graph edits may be accepted.
    require_reasoner_auto_safe: if true, reasoner.safe_to_auto_plan must be true.
    """

    min_confidence: float = 0.55
    min_score: float = 0.62
    max_risk: float = 0.55
    max_cost: float = 0.70
    allow_non_executable: bool = False
    require_reasoner_auto_safe: bool = False
    prefer_low_cost_first: bool = True
    max_decisions: int = 1

    @classmethod
    def from_request(cls, request: Mapping[str, Any]) -> "DecisionPolicy":
        """Build policy from a user request without trusting invalid values."""

        decision = request.get("decision") if isinstance(request.get("decision"), Mapping) else {}
        source: Mapping[str, Any] = decision or request

        def f(name: str, default: float) -> float:
            try:
                return clamp01(float(source.get(name, default)))
            except (TypeError, ValueError):
                return default

        def b(name: str, default: bool) -> bool:
            value = source.get(name, default)
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y", "on"}
            return bool(value)

        def i(name: str, default: int) -> int:
            try:
                return max(1, int(source.get(name, default)))
            except (TypeError, ValueError):
                return default

        return cls(
            min_confidence=f("min_confidence", cls.min_confidence),
            min_score=f("min_score", cls.min_score),
            max_risk=f("max_risk", cls.max_risk),
            max_cost=f("max_cost", cls.max_cost),
            allow_non_executable=b("allow_non_executable", cls.allow_non_executable),
            require_reasoner_auto_safe=b("require_reasoner_auto_safe", cls.require_reasoner_auto_safe),
            prefer_low_cost_first=b("prefer_low_cost_first", cls.prefer_low_cost_first),
            max_decisions=i("max_decisions", cls.max_decisions),
        )

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class DecisionCandidate:
    """One selectable candidate derived from strategy/suggested ops."""

    id: str
    kind: DecisionKind
    target: str = ""
    action: str = ""
    value: Any = None
    op: Optional[Json] = None
    strategy: Optional[Json] = None
    confidence: float = 0.0
    risk: float = 0.5
    cost: float = 0.5
    score: float = 0.0
    reasons: Tuple[str, ...] = ()
    reasons_zh: Tuple[str, ...] = ()

    @property
    def is_executable(self) -> bool:
        return self.kind == DecisionKind.EXECUTABLE_OP and isinstance(self.op, Mapping)

    def to_dict(self) -> Json:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass(frozen=True)
class Decision:
    """Selected decision."""

    id: str
    status: DecisionStatus
    kind: DecisionKind
    target: str = ""
    action: str = ""
    value: Any = None
    op: Optional[Json] = None
    confidence: float = 0.0
    score: float = 0.0
    risk: float = 0.0
    cost: float = 0.0
    reason: str = ""
    reason_zh: str = ""
    guardrails: Tuple[str, ...] = ()
    guardrails_zh: Tuple[str, ...] = ()

    def to_dict(self) -> Json:
        data = asdict(self)
        data["status"] = self.status.value
        data["kind"] = self.kind.value
        return data


@dataclass(frozen=True)
class DecisionResult:
    """Decision Engine output."""

    request_id: str
    status: DecisionStatus
    policy: DecisionPolicy
    decisions: Tuple[Decision, ...]
    candidates: Tuple[DecisionCandidate, ...]
    selected_ops: Tuple[Json, ...] = ()
    requires_review: bool = True
    ask_before: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    trace: Tuple[Json, ...] = ()
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Json:
        return {
            "success": True,
            "request_id": self.request_id,
            "status": self.status.value,
            "policy": self.policy.to_dict(),
            "decisions": [item.to_dict() for item in self.decisions],
            "candidates": [item.to_dict() for item in self.candidates],
            "selected_ops": list(self.selected_ops),
            "requires_review": self.requires_review,
            "ask_before": list(self.ask_before),
            "warnings": list(self.warnings),
            "trace": list(self.trace),
            "created_at": self.created_at,
        }


_RISK_LEVELS = {"none": 0.0, "very_low": 0.1, "low": 0.22, "medium": 0.48, "high": 0.75, "critical": 0.95}
_COST_LEVELS = {"none": 0.0, "very_low": 0.1, "low": 0.22, "medium": 0.48, "high": 0.78, "critical": 0.95}


def _request_id(reasoning: Mapping[str, Any], request: Mapping[str, Any]) -> str:
    value = request.get("request_id") or request.get("id") or reasoning.get("request_id")
    return str(value) if value else str(uuid.uuid4())


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _level_to_float(value: Any, table: Mapping[str, float], default: float) -> float:
    if isinstance(value, (int, float)):
        return clamp01(float(value))
    key = str(value or "").strip().lower()
    return clamp01(table.get(key, default))


def _strategy_id(strategy: Mapping[str, Any], index: int) -> str:
    value = strategy.get("id")
    return str(value) if value else f"strategy.{index + 1}"


def _op_key(op: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (str(op.get("op") or ""), str(op.get("target") or ""), str(op.get("input") or ""))


def _is_directly_executable_op(op: Mapping[str, Any]) -> bool:
    """Current workflow_ops reliably supports semantic set ops."""

    return op.get("op") == "set" and isinstance(op.get("target"), str) and bool(str(op.get("target")).strip())


def _collect_ops(reasoning: Mapping[str, Any]) -> List[Json]:
    ops: List[Json] = []
    for item in _as_list(reasoning.get("suggested_ops")):
        if isinstance(item, Mapping):
            ops.append(dict(item))
    return ops


def build_decision_candidates(reasoning: Mapping[str, Any]) -> List[DecisionCandidate]:
    """Convert Reasoner output into ranked decision candidates."""

    strategies = [dict(s) for s in _as_list(reasoning.get("strategy")) if isinstance(s, Mapping)]
    suggested_ops = _collect_ops(reasoning)
    op_by_target: Dict[Tuple[str, str, str], Json] = {_op_key(op): op for op in suggested_ops}

    candidates: List[DecisionCandidate] = []
    for index, strategy in enumerate(strategies):
        target = str(strategy.get("target") or "")
        action = str(strategy.get("action") or "")
        confidence = clamp01(float(strategy.get("confidence") or 0.0))
        risk = _level_to_float(strategy.get("risk"), _RISK_LEVELS, 0.5)
        cost = _level_to_float(strategy.get("cost"), _COST_LEVELS, 0.5)
        value = strategy.get("value")
        matching_op = None
        for op in suggested_ops:
            if str(op.get("target") or "") == target:
                matching_op = dict(op)
                break

        executable = bool(matching_op and _is_directly_executable_op(matching_op))
        if executable:
            kind = DecisionKind.EXECUTABLE_OP
        elif target and action and value is not None:
            kind = DecisionKind.PLANNER_INTENT
        elif action == "ask" or not target:
            kind = DecisionKind.CLARIFICATION
        else:
            kind = DecisionKind.ADVICE_ONLY

        score = _candidate_score(confidence=confidence, risk=risk, cost=cost, executable=executable, priority=index + 1)
        reasons = [
            f"strategy priority={index + 1}",
            f"confidence={confidence:.2f}",
            f"risk={risk:.2f}",
            f"cost={cost:.2f}",
        ]
        reasons_zh = [
            f"策略优先级={index + 1}",
            f"置信度={confidence:.2f}",
            f"风险={risk:.2f}",
            f"成本={cost:.2f}",
        ]
        if executable:
            reasons.append("direct semantic set op is executable by current workflow_ops")
            reasons_zh.append("当前 workflow_ops 可以直接执行该语义 set 操作")
        elif kind == DecisionKind.ADVICE_ONLY:
            reasons.append("candidate is advice-only and should not be auto-applied")
            reasons_zh.append("该候选属于建议，不应自动应用")

        candidates.append(DecisionCandidate(
            id=_strategy_id(strategy, index),
            kind=kind,
            target=target,
            action=action,
            value=value,
            op=matching_op,
            strategy=strategy,
            confidence=confidence,
            risk=risk,
            cost=cost,
            score=score,
            reasons=tuple(reasons),
            reasons_zh=tuple(reasons_zh),
        ))

    # Include executable ops not represented by strategy, keeping the engine robust.
    represented = {str(c.target) for c in candidates if c.target}
    for index, op in enumerate(suggested_ops):
        target = str(op.get("target") or "")
        if not target or target in represented:
            continue
        executable = _is_directly_executable_op(op)
        kind = DecisionKind.EXECUTABLE_OP if executable else DecisionKind.PLANNER_INTENT
        confidence = clamp01(float(reasoning.get("confidence") or 0.5))
        risk = 0.35 if executable else 0.55
        cost = 0.35
        candidates.append(DecisionCandidate(
            id=f"op.{index + 1}",
            kind=kind,
            target=target,
            action=str(op.get("op") or ""),
            value=op.get("value"),
            op=dict(op),
            confidence=confidence,
            risk=risk,
            cost=cost,
            score=_candidate_score(confidence=confidence, risk=risk, cost=cost, executable=executable, priority=index + 1),
            reasons=("candidate derived directly from suggested_ops",),
            reasons_zh=("候选直接来自 Reasoner 的 suggested_ops",),
        ))

    candidates.sort(key=lambda c: (-c.score, c.risk, c.cost, c.id))
    return candidates


def _candidate_score(*, confidence: float, risk: float, cost: float, executable: bool, priority: int) -> float:
    base = (confidence * 0.58) + ((1.0 - risk) * 0.24) + ((1.0 - cost) * 0.12)
    if executable:
        base += 0.08
    base -= min(max(priority - 1, 0), 5) * 0.025
    return clamp01(base)


def _guardrails(candidate: DecisionCandidate, reasoning: Mapping[str, Any], policy: DecisionPolicy) -> Tuple[List[str], List[str]]:
    guards: List[str] = []
    guards_zh: List[str] = []

    if policy.require_reasoner_auto_safe and not bool(reasoning.get("safe_to_auto_plan", False)):
        guards.append("reasoner did not mark this request as safe_to_auto_plan")
        guards_zh.append("Reasoner 未标记该请求可安全自动规划")
    if candidate.confidence < policy.min_confidence:
        guards.append(f"candidate confidence {candidate.confidence:.2f} < policy min_confidence {policy.min_confidence:.2f}")
        guards_zh.append(f"候选置信度 {candidate.confidence:.2f} 低于阈值 {policy.min_confidence:.2f}")
    if candidate.score < policy.min_score:
        guards.append(f"candidate score {candidate.score:.2f} < policy min_score {policy.min_score:.2f}")
        guards_zh.append(f"候选评分 {candidate.score:.2f} 低于阈值 {policy.min_score:.2f}")
    if candidate.risk > policy.max_risk:
        guards.append(f"candidate risk {candidate.risk:.2f} > policy max_risk {policy.max_risk:.2f}")
        guards_zh.append(f"候选风险 {candidate.risk:.2f} 高于阈值 {policy.max_risk:.2f}")
    if candidate.cost > policy.max_cost:
        guards.append(f"candidate cost {candidate.cost:.2f} > policy max_cost {policy.max_cost:.2f}")
        guards_zh.append(f"候选成本 {candidate.cost:.2f} 高于阈值 {policy.max_cost:.2f}")
    if not candidate.is_executable and not policy.allow_non_executable:
        guards.append("candidate is not a directly executable workflow op")
        guards_zh.append("候选不是当前可直接执行的工作流操作")
    if candidate.kind in {DecisionKind.ADVICE_ONLY, DecisionKind.CLARIFICATION, DecisionKind.NO_ACTION}:
        guards.append(f"candidate kind {candidate.kind.value} requires human/planner review")
        guards_zh.append(f"候选类型 {candidate.kind.value} 需要人工或 Planner 复核")

    for warning in _as_list(reasoning.get("warnings")):
        if isinstance(warning, str) and warning.strip():
            guards.append(f"reasoner warning: {warning.strip()}")
            guards_zh.append(f"Reasoner 警告：{warning.strip()}")

    return guards, guards_zh


def select_decisions(reasoning: Mapping[str, Any], request: Optional[Mapping[str, Any]] = None) -> DecisionResult:
    """Select the best decision(s) from a ReasoningResult-like mapping."""

    request = request or {}
    policy = DecisionPolicy.from_request(request)
    request_id = _request_id(reasoning, request)
    candidates = build_decision_candidates(reasoning)
    trace: List[Json] = [
        {"stage": "candidate_build", "message": "Built decision candidates from Reasoner output.", "message_zh": "已从 Reasoner 输出构建决策候选。", "count": len(candidates)},
        {"stage": "policy", "message": "Loaded decision policy.", "message_zh": "已加载决策策略。", "policy": policy.to_dict()},
    ]

    ask_before = [str(x) for x in _as_list(reasoning.get("ask_before")) if isinstance(x, str)]
    warnings = [str(x) for x in _as_list(reasoning.get("warnings")) if isinstance(x, str)]

    if not candidates:
        decision = Decision(
            id="decision.none",
            status=DecisionStatus.NEEDS_CLARIFICATION,
            kind=DecisionKind.NO_ACTION,
            reason="No candidate strategy or executable operation was produced.",
            reason_zh="Reasoner 没有产生可选择的策略或可执行操作。",
            guardrails=("no candidates",),
            guardrails_zh=("没有候选项",),
        )
        return DecisionResult(
            request_id=request_id,
            status=DecisionStatus.NEEDS_CLARIFICATION,
            policy=policy,
            decisions=(decision,),
            candidates=(),
            requires_review=True,
            ask_before=tuple(ask_before or ["Please clarify the intended workflow change."]),
            warnings=tuple(warnings),
            trace=tuple(trace),
        )

    selected: List[Decision] = []
    selected_ops: List[Json] = []
    for candidate in candidates:
        guards, guards_zh = _guardrails(candidate, reasoning, policy)
        accepted = not guards
        status = DecisionStatus.ACCEPTED if accepted else DecisionStatus.NEEDS_REVIEW
        reason = _decision_reason(candidate, accepted)
        reason_zh = _decision_reason_zh(candidate, accepted)
        decision = Decision(
            id=f"decision.{len(selected) + 1}",
            status=status,
            kind=candidate.kind,
            target=candidate.target,
            action=candidate.action,
            value=candidate.value,
            op=dict(candidate.op) if isinstance(candidate.op, Mapping) else None,
            confidence=candidate.confidence,
            score=candidate.score,
            risk=candidate.risk,
            cost=candidate.cost,
            reason=reason,
            reason_zh=reason_zh,
            guardrails=tuple(guards),
            guardrails_zh=tuple(guards_zh),
        )
        selected.append(decision)
        if accepted and candidate.is_executable and candidate.op:
            selected_ops.append(dict(candidate.op))
        if len(selected) >= policy.max_decisions:
            break

    overall_status = _overall_status(selected, reasoning)
    requires_review = overall_status != DecisionStatus.ACCEPTED
    if requires_review and not ask_before:
        ask_before.append("Review the selected decision before applying it automatically.")

    trace.append({
        "stage": "selection",
        "message": "Selected final decision candidates.",
        "message_zh": "已选择最终决策候选。",
        "status": overall_status.value,
        "selected_count": len(selected),
        "selected_ops": len(selected_ops),
    })

    return DecisionResult(
        request_id=request_id,
        status=overall_status,
        policy=policy,
        decisions=tuple(selected),
        candidates=tuple(candidates),
        selected_ops=tuple(selected_ops),
        requires_review=requires_review,
        ask_before=tuple(ask_before),
        warnings=tuple(warnings),
        trace=tuple(trace),
    )


def _decision_reason(candidate: DecisionCandidate, accepted: bool) -> str:
    if accepted:
        return f"Selected {candidate.target or candidate.kind.value} because it has the best confidence/risk/cost balance."
    return f"Selected {candidate.target or candidate.kind.value} as the leading candidate, but guardrails require review."


def _decision_reason_zh(candidate: DecisionCandidate, accepted: bool) -> str:
    if accepted:
        return f"选择 {candidate.target or candidate.kind.value}，因为它在置信度、风险和成本之间最均衡。"
    return f"{candidate.target or candidate.kind.value} 是当前领先候选，但护栏要求先复核。"


def _overall_status(decisions: Sequence[Decision], reasoning: Mapping[str, Any]) -> DecisionStatus:
    if not decisions:
        return DecisionStatus.NEEDS_CLARIFICATION
    first = decisions[0]
    if first.status == DecisionStatus.ACCEPTED:
        return DecisionStatus.ACCEPTED
    problem = _as_mapping(reasoning.get("problem"))
    if str(problem.get("normalized_goal") or "") == "unknown":
        return DecisionStatus.NEEDS_CLARIFICATION
    if first.kind == DecisionKind.CLARIFICATION:
        return DecisionStatus.NEEDS_CLARIFICATION
    return DecisionStatus.NEEDS_REVIEW


def decide_from_reasoning(reasoning: Mapping[str, Any], request: Optional[Mapping[str, Any]] = None) -> Json:
    """Convenience wrapper returning plain JSON."""

    return select_decisions(reasoning, request=request).to_dict()


__all__ = [
    "Decision",
    "DecisionCandidate",
    "DecisionKind",
    "DecisionPolicy",
    "DecisionResult",
    "DecisionStatus",
    "build_decision_candidates",
    "decide_from_reasoning",
    "select_decisions",
]
