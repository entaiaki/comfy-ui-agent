#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory.models

Typed data models for Agent Memory.

English -> 中文对应：
- Memory -> 记忆：把 Agent 的请求、推理、规划、结果保存为可检索经验。
- Experience -> 经验条目：一次完整或半完整操作记录。
- Outcome -> 结果：成功、失败、用户是否满意等反馈。
- Retrieval -> 检索：按问题、目标、标签、工作流寻找相似经验。

Design boundary:
This module contains only serializable data models. It does not read/write files,
call ComfyUI, mutate workflows, or call any LLM.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Json = Dict[str, Any]


class ExperienceKind(str, Enum):
    """High-level category of an experience."""

    REASONING = "reasoning"
    PLANNING = "planning"
    APPLY = "apply"
    FEEDBACK = "feedback"
    ERROR = "error"
    NOTE = "note"


class OutcomeStatus(str, Enum):
    """User/system outcome status."""

    UNKNOWN = "unknown"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ExperienceOutcome:
    """Result information for a memory entry."""

    status: str = OutcomeStatus.UNKNOWN.value
    score: Optional[float] = None
    message: str = ""
    message_zh: str = ""
    artifacts: Tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> Json:
        data = asdict(self)
        data["artifacts"] = list(self.artifacts)
        return data

    @staticmethod
    def from_mapping(data: Mapping[str, Any] | None) -> "ExperienceOutcome":
        if not data:
            return ExperienceOutcome()
        artifacts = data.get("artifacts") or ()
        if isinstance(artifacts, str):
            artifacts = (artifacts,)
        return ExperienceOutcome(
            status=str(data.get("status") or OutcomeStatus.UNKNOWN.value),
            score=_optional_float(data.get("score")),
            message=str(data.get("message") or ""),
            message_zh=str(data.get("message_zh") or data.get("messageZh") or ""),
            artifacts=tuple(str(x) for x in artifacts if x is not None),
            error=str(data.get("error") or ""),
        )


@dataclass(frozen=True)
class ExperienceEntry:
    """One durable agent memory record.

    The payload fields intentionally stay generic so Reasoner/Planner/Executor can
    evolve independently without requiring schema migrations for every detail.
    Stable indexing fields are duplicated at the top level for fast retrieval.
    """

    id: str
    created_at: float
    kind: str
    workflow: str = ""
    problem: str = ""
    goal: str = ""
    intent: str = ""
    summary: str = ""
    summary_zh: str = ""
    tags: Tuple[str, ...] = ()
    confidence: Optional[float] = None
    request: Json = field(default_factory=dict)
    reasoning: Json = field(default_factory=dict)
    plan: Json = field(default_factory=dict)
    ops: Tuple[Json, ...] = ()
    outcome: ExperienceOutcome = field(default_factory=ExperienceOutcome)
    metadata: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["ops"] = [dict(op) for op in self.ops]
        data["outcome"] = self.outcome.to_dict()
        return data

    @staticmethod
    def from_mapping(data: Mapping[str, Any]) -> "ExperienceEntry":
        tags = data.get("tags") or ()
        if isinstance(tags, str):
            tags = tuple(_split_tags(tags))
        ops = data.get("ops") or ()
        if isinstance(ops, Mapping):
            ops = (dict(ops),)
        return ExperienceEntry(
            id=str(data.get("id") or uuid.uuid4()),
            created_at=float(data.get("created_at") or data.get("timestamp") or time.time()),
            kind=str(data.get("kind") or ExperienceKind.NOTE.value),
            workflow=str(data.get("workflow") or ""),
            problem=str(data.get("problem") or ""),
            goal=str(data.get("goal") or ""),
            intent=str(data.get("intent") or ""),
            summary=str(data.get("summary") or ""),
            summary_zh=str(data.get("summary_zh") or data.get("summaryZh") or ""),
            tags=tuple(str(x).strip() for x in tags if str(x).strip()),
            confidence=_optional_float(data.get("confidence")),
            request=dict(data.get("request") or {}),
            reasoning=dict(data.get("reasoning") or {}),
            plan=dict(data.get("plan") or {}),
            ops=tuple(dict(op) for op in ops if isinstance(op, Mapping)),
            outcome=ExperienceOutcome.from_mapping(data.get("outcome") if isinstance(data.get("outcome"), Mapping) else None),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MemoryQuery:
    """Search criteria for memory retrieval."""

    text: str = ""
    workflow: str = ""
    problem: str = ""
    goal: str = ""
    intent: str = ""
    tags: Tuple[str, ...] = ()
    kind: str = ""
    outcome_status: str = ""
    min_score: Optional[float] = None
    limit: int = 10

    @staticmethod
    def from_mapping(data: Mapping[str, Any] | None) -> "MemoryQuery":
        data = data or {}
        tags = data.get("tags") or ()
        if isinstance(tags, str):
            tags = tuple(_split_tags(tags))
        limit = int(data.get("limit") or 10)
        return MemoryQuery(
            text=str(data.get("text") or data.get("query") or ""),
            workflow=str(data.get("workflow") or ""),
            problem=str(data.get("problem") or ""),
            goal=str(data.get("goal") or ""),
            intent=str(data.get("intent") or ""),
            tags=tuple(str(x).strip() for x in tags if str(x).strip()),
            kind=str(data.get("kind") or ""),
            outcome_status=str(data.get("outcome_status") or data.get("status") or ""),
            min_score=_optional_float(data.get("min_score")),
            limit=max(1, min(limit, 100)),
        )


@dataclass(frozen=True)
class MemoryMatch:
    """One search result with a deterministic relevance score."""

    entry: ExperienceEntry
    score: float
    reasons: Tuple[str, ...] = ()

    def to_dict(self, *, include_payload: bool = True) -> Json:
        data = {
            "score": round(float(self.score), 6),
            "reasons": list(self.reasons),
            "entry": self.entry.to_dict(),
        }
        if not include_payload:
            data["entry"].pop("request", None)
            data["entry"].pop("reasoning", None)
            data["entry"].pop("plan", None)
        return data


def make_entry_id() -> str:
    return str(uuid.uuid4())


def now_ts() -> float:
    return time.time()


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _split_tags(value: str) -> List[str]:
    return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
