#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reasoning.models

Typed data model for the ComfyUI Agent reasoning layer.

English -> 中文对应：
- Reasoner -> 推理器：根据用户问题、工作流语义、知识证据，判断下一步应该朝哪个方向改。
- Observation -> 观察：从 workflow / semantic context 中读到的事实。
- Evidence -> 证据：知识库或上下文中支持某个判断的依据。
- Hypothesis -> 假设：对问题成因或改进方向的候选解释。
- Strategy -> 策略：可交给 Planner 的高层修改意图，不直接修改 workflow。
- Reasoning Trace -> 推理轨迹：可解释的中间步骤。

Design boundary:
This package is deterministic and side-effect free. It must not call ComfyUI,
write files, or mutate workflow JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

Json = Dict[str, Any]


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceKind(str, Enum):
    WORKFLOW = "workflow"
    KNOWLEDGE = "knowledge"
    USER = "user"
    HEURISTIC = "heuristic"
    WARNING = "warning"


@dataclass(frozen=True)
class ProblemSpec:
    """Normalized user problem / goal."""

    raw_text: str
    normalized_goal: str
    labels: Tuple[str, ...] = ()
    language: str = "unknown"
    confidence: float = 0.5

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class Observation:
    """A deterministic fact observed from workflow semantics or request."""

    key: str
    value: Any
    source: str = "workflow"
    severity: Severity = Severity.INFO
    note: str = ""

    def to_dict(self) -> Json:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data


@dataclass(frozen=True)
class Evidence:
    """Evidence supporting or weakening a hypothesis."""

    id: str
    kind: EvidenceKind
    summary: str
    summary_zh: str = ""
    weight: float = 0.5
    source: str = ""
    target: str = ""
    data: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass(frozen=True)
class Hypothesis:
    """Candidate explanation or improvement direction."""

    id: str
    title: str
    title_zh: str
    goal: str
    target: str = ""
    action: str = ""
    rationale: str = ""
    rationale_zh: str = ""
    base_score: float = 0.5
    confidence: float = 0.5
    cost: float = 0.5
    risk: float = 0.2
    evidence_ids: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class ScoredHypothesis:
    """A hypothesis after scoring."""

    hypothesis: Hypothesis
    score: float
    confidence: float
    cost: float
    risk: float
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Json:
        return {
            "hypothesis": self.hypothesis.to_dict(),
            "score": self.score,
            "confidence": self.confidence,
            "cost": self.cost,
            "risk": self.risk,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class StrategyStep:
    """High-level strategy step. This is NOT a workflow op yet."""

    id: str
    target: str
    action: str
    value: Any = None
    priority: int = 100
    confidence: float = 0.5
    reason: str = ""
    reason_zh: str = ""
    expected_effect: str = ""
    expected_effect_zh: str = ""
    risk: str = "low"
    cost: str = "low"

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class ReasoningTraceStep:
    """One explainable step in the reasoning pipeline."""

    stage: str
    message: str
    message_zh: str = ""
    data: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class ReasoningResult:
    """Final output from Reasoner.

    It deliberately contains strategy steps and optional semantic ops, but the
    reasoner itself does not apply them. The bridge/planner decides what to do.
    """

    request_id: str
    problem: ProblemSpec
    observations: Tuple[Observation, ...]
    evidence: Tuple[Evidence, ...]
    hypotheses: Tuple[ScoredHypothesis, ...]
    strategy: Tuple[StrategyStep, ...]
    suggested_ops: Tuple[Json, ...] = ()
    confidence: float = 0.0
    confidence_band: ConfidenceBand = ConfidenceBand.LOW
    safe_to_auto_plan: bool = False
    ask_before: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    trace: Tuple[ReasoningTraceStep, ...] = ()

    def to_dict(self) -> Json:
        return {
            "request_id": self.request_id,
            "problem": self.problem.to_dict(),
            "observations": [item.to_dict() for item in self.observations],
            "evidence": [item.to_dict() for item in self.evidence],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "strategy": [item.to_dict() for item in self.strategy],
            "suggested_ops": list(self.suggested_ops),
            "confidence": self.confidence,
            "confidence_band": self.confidence_band.value,
            "safe_to_auto_plan": self.safe_to_auto_plan,
            "ask_before": list(self.ask_before),
            "warnings": list(self.warnings),
            "trace": [item.to_dict() for item in self.trace],
        }


def clamp01(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def confidence_band(value: float) -> ConfidenceBand:
    value = clamp01(value)
    if value >= 0.75:
        return ConfidenceBand.HIGH
    if value >= 0.45:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def average(values: Sequence[float], default: float = 0.0) -> float:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return default
    return sum(clean) / len(clean)
