#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""planner.models

Typed planning data model for the ComfyUI Agent framework.

English -> 中文对应：
- Planner -> 规划器：把 Reasoner 的高层策略变成可执行 workflow ops。
- Plan Candidate -> 候选规划：一种可能的修改方案。
- Cost -> 代价：修改带来的速度、风险、侵入性等综合成本。
- Risk -> 风险：自动执行后出错或偏离用户目标的可能性。
- Tradeoff -> 权衡：例如更清晰通常更慢。

Design boundary:
The planner is deterministic and side-effect free. It may inspect workflow
semantics and produce ops, but it must not call ComfyUI or mutate files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Json = Dict[str, Any]


class PlanStatus(str, Enum):
    DRAFT = "draft"
    VALID = "valid"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNSAFE = "unsafe"


class PlanPriority(str, Enum):
    QUALITY = "quality"
    SPEED = "speed"
    BALANCED = "balanced"
    SAFETY = "safety"


@dataclass(frozen=True)
class PlanAction:
    """One semantic action before it becomes a raw workflow op."""

    id: str
    target: str
    operation: str = "set"
    value: Any = None
    reason: str = ""
    reason_zh: str = ""
    source: str = "planner"
    confidence: float = 0.5
    estimated_cost: float = 0.0
    estimated_risk: float = 0.0
    tags: Tuple[str, ...] = ()

    def to_op(self) -> Json:
        if self.operation == "set":
            return {"op": "set", "target": self.target, "value": self.value}
        if self.operation == "append_text":
            return {"op": "append_text", "target": self.target, "text": self.value, "separator": ", "}
        return {"op": self.operation, "target": self.target, "value": self.value}

    def to_dict(self) -> Json:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["op"] = self.to_op()
        return data


@dataclass(frozen=True)
class PlanCost:
    """Normalized cost/risk estimate for a plan. Range is usually 0..1."""

    compute_cost: float = 0.0
    edit_cost: float = 0.0
    risk_cost: float = 0.0
    user_confirmation_cost: float = 0.0
    total: float = 0.0
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Json:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


@dataclass(frozen=True)
class PlanValidationIssue:
    level: str
    message: str
    message_zh: str = ""
    target: str = ""
    data: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class PlanCandidate:
    """A complete candidate plan."""

    id: str
    title: str
    title_zh: str
    priority: PlanPriority
    actions: Tuple[PlanAction, ...]
    cost: PlanCost
    score: float
    confidence: float
    status: PlanStatus = PlanStatus.DRAFT
    summary: str = ""
    summary_zh: str = ""
    tradeoffs: Tuple[str, ...] = ()
    issues: Tuple[PlanValidationIssue, ...] = ()
    trace: Tuple[Json, ...] = ()

    @property
    def ops(self) -> List[Json]:
        return [action.to_op() for action in self.actions]

    def to_dict(self) -> Json:
        return {
            "id": self.id,
            "title": self.title,
            "title_zh": self.title_zh,
            "priority": self.priority.value,
            "actions": [a.to_dict() for a in self.actions],
            "ops": self.ops,
            "cost": self.cost.to_dict(),
            "score": self.score,
            "confidence": self.confidence,
            "status": self.status.value,
            "summary": self.summary,
            "summary_zh": self.summary_zh,
            "tradeoffs": list(self.tradeoffs),
            "issues": [i.to_dict() for i in self.issues],
            "trace": list(self.trace),
        }


@dataclass(frozen=True)
class PlanRequest:
    """Normalized planner input."""

    request_id: str
    goal: str
    text: str = ""
    priority: PlanPriority = PlanPriority.BALANCED
    intent: str = ""
    parameters: Json = field(default_factory=dict)
    reasoner_result: Json = field(default_factory=dict)
    max_candidates: int = 3
    allow_text_append_ops: bool = False

    def to_dict(self) -> Json:
        data = asdict(self)
        data["priority"] = self.priority.value
        return data


@dataclass(frozen=True)
class PlanResult:
    """Final planner output."""

    request_id: str
    success: bool
    selected: Optional[PlanCandidate]
    candidates: Tuple[PlanCandidate, ...]
    warnings: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    trace: Tuple[Json, ...] = ()

    def to_dict(self) -> Json:
        return {
            "request_id": self.request_id,
            "success": self.success,
            "selected": self.selected.to_dict() if self.selected else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "ops": self.selected.ops if self.selected else [],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "trace": list(self.trace),
        }


def clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def average(values: Sequence[float], default: float = 0.0) -> float:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return default
    return sum(clean) / len(clean)
