#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_memory.py

v14 Experience Memory for the ComfyUI Agent framework.

English -> 中文对应：
- Experience Memory -> 经验记忆：记录 Agent 的历史问题、决策、执行计划和人工/系统反馈。
- Experience -> 经验条目：一次可复用的历史案例，不等同于聊天记录。
- Retrieval -> 检索：根据当前问题、目标、工作流和目标参数找相似经验。
- Outcome -> 结果：success / failed / partial / unknown，用于后续排序和学习。

Design boundary / 设计边界：
This module is deterministic and file-based. It does not call ComfyUI, does not
inspect images, does not mutate workflow JSON, and does not decide what to run.
It only stores and retrieves structured experience so Reasoner / Pipeline can
use it later.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

Json = Dict[str, Any]

_MEMORY_SCHEMA_VERSION = "1.0"
_DEFAULT_MEMORY_FILENAME = "agent_experience_memory.jsonl"
_OUTCOME_WEIGHT = {
    "success": 1.0,
    "partial": 0.72,
    "unknown": 0.55,
    "pending": 0.50,
    "failed": 0.18,
    "rejected": 0.10,
}


def _now() -> float:
    return time.time()


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_lower(value: Any) -> str:
    return _safe_str(value).lower()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def _normalize_outcome(value: Any) -> str:
    text = _safe_lower(value)
    aliases = {
        "ok": "success",
        "pass": "success",
        "passed": "success",
        "done": "success",
        "good": "success",
        "成功": "success",
        "有效": "success",
        "partial_success": "partial",
        "partly": "partial",
        "部分成功": "partial",
        "fail": "failed",
        "error": "failed",
        "bad": "failed",
        "失败": "failed",
        "无效": "failed",
        "reject": "rejected",
        "rejected": "rejected",
        "pending": "pending",
        "待确认": "pending",
        "unknown": "unknown",
        "": "unknown",
    }
    return aliases.get(text, text if text in _OUTCOME_WEIGHT else "unknown")


def _tokenize(text: Any) -> Tuple[str, ...]:
    raw = _safe_lower(text)
    if not raw:
        return ()
    for sep in ["_", "-", ".", "/", "\\", ":", ";", ",", "，", "。", "、", "|", "(", ")", "[", "]", "{", "}", "\n", "\t"]:
        raw = raw.replace(sep, " ")
    tokens = [part for part in raw.split(" ") if part]
    # Keep short Chinese phrases as useful tokens by also slicing text into a
    # few common user-problem fragments. This is intentionally simple and
    # deterministic; no external NLP dependency is required.
    chinese_hints = []
    for hint in ["太糊", "模糊", "更清晰", "动漫", "二次元", "太慢", "更快", "过曝", "太暗", "提示词"]:
        if hint in raw:
            chinese_hints.append(hint)
    return tuple(dict.fromkeys(tokens + chinese_hints))


def _stable_hash(data: Mapping[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _workflow_fingerprint(workflow: Optional[Mapping[str, Any]]) -> str:
    if not workflow:
        return ""
    nodes: List[str] = []
    for node_id, node in sorted(workflow.items(), key=lambda item: str(item[0])):
        if isinstance(node, Mapping):
            nodes.append(f"{node_id}:{node.get('class_type','')}")
    return hashlib.sha256("|".join(nodes).encode("utf-8")).hexdigest()[:16] if nodes else ""


def _extract_goal(request: Mapping[str, Any]) -> str:
    for key in ("goal", "normalized_goal", "problem", "intent", "instruction", "text", "query"):
        value = _safe_str(request.get(key))
        if value:
            return value
    reasoning = _as_mapping(request.get("reasoning"))
    problem = _as_mapping(reasoning.get("problem"))
    return _safe_str(problem.get("normalized_goal") or problem.get("raw_text") or reasoning.get("goal"))


def _extract_targets_from_ops(ops: Iterable[Any]) -> Tuple[str, ...]:
    targets: List[str] = []
    for item in ops:
        op = _as_mapping(item)
        target = _safe_str(op.get("target") or op.get("input") or op.get("node"))
        if target:
            targets.append(target)
    return tuple(dict.fromkeys(targets))


def _extract_selected_ops(request: Mapping[str, Any]) -> Tuple[Json, ...]:
    candidates: List[Any] = []
    for key in ("ops", "selected_ops"):
        candidates.extend(_as_list(request.get(key)))
    decision = _as_mapping(request.get("decision"))
    candidates.extend(_as_list(decision.get("selected_ops")))
    pipeline = _as_mapping(request.get("pipeline_result"))
    candidates.extend(_as_list(pipeline.get("selected_ops")))
    plan = _as_mapping(request.get("plan"))
    candidates.extend(_as_list(plan.get("ops")))

    seen: set[Tuple[str, str, str]] = set()
    result: List[Json] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        op = dict(item)
        key = (_safe_str(op.get("op")), _safe_str(op.get("target")), json.dumps(op.get("value"), ensure_ascii=False, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        result.append(op)
    return tuple(result)


def _compact(value: Any, *, max_chars: int = 4000) -> Any:
    """Keep memory records useful without storing huge workflow/image payloads."""

    if isinstance(value, Mapping):
        blocked = {"workflow", "workflow_after", "workflow_before", "image", "images", "image_bytes", "base64", "preview"}
        out: Json = {}
        for key, item in value.items():
            if str(key) in blocked:
                continue
            out[str(key)] = _compact(item, max_chars=max_chars)
        return out
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars) for item in value[:30]]
    if isinstance(value, tuple):
        return [_compact(item, max_chars=max_chars) for item in list(value)[:30]]
    if isinstance(value, str):
        return value[:max_chars]
    return value


def default_memory_path(out_dir: Any = None) -> Path:
    """Return the default JSONL path for experience memory."""

    base = Path(out_dir) if out_dir else Path("generated_outputs")
    return base / _DEFAULT_MEMORY_FILENAME


@dataclass(frozen=True)
class ExperienceRecord:
    """One reusable historical Agent experience."""

    id: str
    schema_version: str
    created_at: float
    workflow_name: str = ""
    workflow_fingerprint: str = ""
    goal: str = ""
    problem: str = ""
    intent: str = ""
    outcome: str = "unknown"
    rating: float = 0.0
    confidence: float = 0.0
    targets: Tuple[str, ...] = ()
    selected_ops: Tuple[Json, ...] = ()
    notes: str = ""
    tags: Tuple[str, ...] = ()
    reasoning_summary: Json = field(default_factory=dict)
    decision_summary: Json = field(default_factory=dict)
    plan_summary: Json = field(default_factory=dict)
    metrics: Json = field(default_factory=dict)
    source: str = "manual"

    def searchable_text(self) -> str:
        parts = [
            self.workflow_name,
            self.workflow_fingerprint,
            self.goal,
            self.problem,
            self.intent,
            self.outcome,
            self.notes,
            *self.tags,
            *self.targets,
        ]
        for op in self.selected_ops:
            parts.append(_safe_str(op.get("op")))
            parts.append(_safe_str(op.get("target")))
            parts.append(_safe_str(op.get("value")))
        return " ".join(part for part in parts if part)

    def to_dict(self) -> Json:
        data = asdict(self)
        data["targets"] = list(self.targets)
        data["selected_ops"] = [dict(op) for op in self.selected_ops]
        data["tags"] = list(self.tags)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperienceRecord":
        return cls(
            id=_safe_str(data.get("id")) or str(uuid.uuid4()),
            schema_version=_safe_str(data.get("schema_version")) or _MEMORY_SCHEMA_VERSION,
            created_at=float(data.get("created_at") or _now()),
            workflow_name=_safe_str(data.get("workflow_name")),
            workflow_fingerprint=_safe_str(data.get("workflow_fingerprint")),
            goal=_safe_str(data.get("goal")),
            problem=_safe_str(data.get("problem")),
            intent=_safe_str(data.get("intent")),
            outcome=_normalize_outcome(data.get("outcome")),
            rating=_clamp01(float(data.get("rating") or 0.0)),
            confidence=_clamp01(float(data.get("confidence") or 0.0)),
            targets=tuple(_safe_str(x) for x in _as_list(data.get("targets")) if _safe_str(x)),
            selected_ops=tuple(dict(x) for x in _as_list(data.get("selected_ops")) if isinstance(x, Mapping)),
            notes=_safe_str(data.get("notes")),
            tags=tuple(_safe_str(x) for x in _as_list(data.get("tags")) if _safe_str(x)),
            reasoning_summary=dict(_as_mapping(data.get("reasoning_summary"))),
            decision_summary=dict(_as_mapping(data.get("decision_summary"))),
            plan_summary=dict(_as_mapping(data.get("plan_summary"))),
            metrics=dict(_as_mapping(data.get("metrics"))),
            source=_safe_str(data.get("source")) or "manual",
        )


@dataclass(frozen=True)
class MemoryMatch:
    record: ExperienceRecord
    score: float
    reasons: Tuple[str, ...] = ()
    reasons_zh: Tuple[str, ...] = ()

    def to_dict(self, *, include_record: bool = True) -> Json:
        data: Json = {
            "id": self.record.id,
            "score": self.score,
            "outcome": self.record.outcome,
            "goal": self.record.goal,
            "workflow_name": self.record.workflow_name,
            "targets": list(self.record.targets),
            "selected_ops": [dict(op) for op in self.record.selected_ops],
            "confidence": self.record.confidence,
            "rating": self.record.rating,
            "reasons": list(self.reasons),
            "reasons_zh": list(self.reasons_zh),
            "created_at": self.record.created_at,
        }
        if include_record:
            data["record"] = self.record.to_dict()
        return data


@dataclass(frozen=True)
class MemorySearchQuery:
    text: str = ""
    goal: str = ""
    workflow_name: str = ""
    workflow_fingerprint: str = ""
    target: str = ""
    outcome: str = ""
    limit: int = 8
    min_score: float = 0.12
    include_records: bool = True

    @classmethod
    def from_request(cls, request: Mapping[str, Any]) -> "MemorySearchQuery":
        try:
            limit = max(1, min(50, int(request.get("limit", 8))))
        except (TypeError, ValueError):
            limit = 8
        try:
            min_score = _clamp01(float(request.get("min_score", 0.12)))
        except (TypeError, ValueError):
            min_score = 0.12
        return cls(
            text=_safe_str(request.get("text") or request.get("problem") or request.get("instruction") or request.get("query")),
            goal=_safe_str(request.get("goal") or request.get("normalized_goal") or _extract_goal(request)),
            workflow_name=_safe_str(request.get("workflow_name") or request.get("workflow")),
            workflow_fingerprint=_safe_str(request.get("workflow_fingerprint")),
            target=_safe_str(request.get("target")),
            outcome=_normalize_outcome(request.get("outcome")) if request.get("outcome") else "",
            limit=limit,
            min_score=min_score,
            include_records=bool(request.get("include_records", True)),
        )

    def to_dict(self) -> Json:
        return asdict(self)


class ExperienceMemoryStore:
    """Small append-only JSONL memory store.

    JSONL keeps the store robust: every record is one independent line. A bad
    line does not destroy the whole memory file, and appending is cheap.
    """

    def __init__(self, memory_path: Any):
        self.memory_path = Path(memory_path)

    def ensure_parent(self) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, *, max_records: Optional[int] = None) -> List[ExperienceRecord]:
        if not self.memory_path.exists():
            return []
        records: List[ExperienceRecord] = []
        with self.memory_path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                    records.append(ExperienceRecord.from_dict(data))
                except Exception:
                    # Do not fail the whole memory because of one corrupted line.
                    continue
        if max_records is not None and max_records >= 0:
            return records[-max_records:]
        return records

    def append(self, record: ExperienceRecord) -> None:
        self.ensure_parent()
        line = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)
        with self.memory_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def rewrite(self, records: Sequence[ExperienceRecord]) -> None:
        self.ensure_parent()
        fd, temp_name = tempfile.mkstemp(prefix=self.memory_path.name, suffix=".tmp", dir=str(self.memory_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            os.replace(temp_name, self.memory_path)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass

    def stats(self) -> Json:
        records = self.load()
        outcome_counts: Dict[str, int] = {}
        target_counts: Dict[str, int] = {}
        goals: Dict[str, int] = {}
        for record in records:
            outcome_counts[record.outcome] = outcome_counts.get(record.outcome, 0) + 1
            if record.goal:
                goals[record.goal] = goals.get(record.goal, 0) + 1
            for target in record.targets:
                target_counts[target] = target_counts.get(target, 0) + 1
        return {
            "success": True,
            "memory_path": str(self.memory_path),
            "exists": self.memory_path.exists(),
            "records": len(records),
            "outcomes": dict(sorted(outcome_counts.items())),
            "top_targets": sorted(target_counts.items(), key=lambda item: (-item[1], item[0]))[:20],
            "top_goals": sorted(goals.items(), key=lambda item: (-item[1], item[0]))[:20],
            "schema_version": _MEMORY_SCHEMA_VERSION,
        }


def build_experience_record(request: Mapping[str, Any], *, workflow_name: str = "", workflow: Optional[Mapping[str, Any]] = None) -> ExperienceRecord:
    """Normalize arbitrary pipeline/decision/apply data into one memory record."""

    pipeline_result = _as_mapping(request.get("pipeline_result"))
    reasoning = dict(_as_mapping(request.get("reasoning") or pipeline_result.get("reasoning")))
    decision = dict(_as_mapping(request.get("decision") or pipeline_result.get("decision")))
    plan = dict(_as_mapping(request.get("plan") or pipeline_result.get("plan")))
    problem = _as_mapping(reasoning.get("problem"))

    selected_ops = _extract_selected_ops(request)
    targets = _extract_targets_from_ops(selected_ops) or tuple(_safe_str(x) for x in _as_list(request.get("targets")) if _safe_str(x))
    goal = _safe_str(request.get("goal") or request.get("normalized_goal") or problem.get("normalized_goal") or _extract_goal(request))
    outcome = _normalize_outcome(request.get("outcome") or request.get("result") or request.get("status"))

    confidence = 0.0
    for source in (decision, reasoning, pipeline_result):
        try:
            confidence = max(confidence, _clamp01(float(source.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            pass
    if decision.get("decisions"):
        for item in _as_list(decision.get("decisions")):
            m = _as_mapping(item)
            try:
                confidence = max(confidence, _clamp01(float(m.get("confidence") or 0.0)))
            except (TypeError, ValueError):
                pass

    base: Json = {
        "workflow_name": workflow_name or _safe_str(request.get("workflow_name") or request.get("workflow")),
        "workflow_fingerprint": _safe_str(request.get("workflow_fingerprint")) or _workflow_fingerprint(workflow),
        "goal": goal,
        "problem": _safe_str(request.get("problem") or problem.get("raw_text") or request.get("text") or request.get("instruction")),
        "intent": _safe_str(request.get("intent")),
        "outcome": outcome,
        "targets": list(targets),
        "selected_ops": [dict(op) for op in selected_ops],
    }
    record_id = _safe_str(request.get("experience_id") or request.get("id")) or _stable_hash({**base, "created_at_hint": request.get("request_id") or request.get("created_at") or ""})

    rating_raw = request.get("rating")
    try:
        rating = _clamp01(float(rating_raw)) if rating_raw is not None else _OUTCOME_WEIGHT.get(outcome, 0.55)
    except (TypeError, ValueError):
        rating = _OUTCOME_WEIGHT.get(outcome, 0.55)

    return ExperienceRecord(
        id=record_id,
        schema_version=_MEMORY_SCHEMA_VERSION,
        created_at=float(request.get("created_at") or _now()),
        workflow_name=base["workflow_name"],
        workflow_fingerprint=base["workflow_fingerprint"],
        goal=goal,
        problem=base["problem"],
        intent=base["intent"],
        outcome=outcome,
        rating=rating,
        confidence=confidence,
        targets=tuple(targets),
        selected_ops=selected_ops,
        notes=_safe_str(request.get("notes") or request.get("feedback")),
        tags=tuple(_safe_str(x) for x in _as_list(request.get("tags")) if _safe_str(x)),
        reasoning_summary=dict(_compact(reasoning)),
        decision_summary=dict(_compact(decision)),
        plan_summary=dict(_compact(plan)),
        metrics=dict(_compact(_as_mapping(request.get("metrics")))),
        source=_safe_str(request.get("source")) or "manual",
    )


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left = set(a)
    right = set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_record(record: ExperienceRecord, query: MemorySearchQuery) -> MemoryMatch:
    reasons: List[str] = []
    reasons_zh: List[str] = []
    score = 0.0

    q_text_tokens = _tokenize(" ".join([query.text, query.goal, query.target]))
    r_tokens = _tokenize(record.searchable_text())
    token_score = _jaccard(q_text_tokens, r_tokens)
    if token_score > 0:
        score += 0.32 * token_score
        reasons.append(f"text_overlap={token_score:.2f}")
        reasons_zh.append(f"文本/目标相似度 {token_score:.2f}")

    if query.goal and _safe_lower(query.goal) == _safe_lower(record.goal):
        score += 0.22
        reasons.append("same_goal")
        reasons_zh.append("目标一致")
    elif query.goal and _safe_lower(query.goal) in _safe_lower(record.searchable_text()):
        score += 0.12
        reasons.append("related_goal")
        reasons_zh.append("目标相关")

    if query.target and query.target in record.targets:
        score += 0.20
        reasons.append("same_target")
        reasons_zh.append("修改目标一致")
    elif query.target and any(query.target in target or target in query.target for target in record.targets):
        score += 0.12
        reasons.append("related_target")
        reasons_zh.append("修改目标相关")

    if query.workflow_fingerprint and query.workflow_fingerprint == record.workflow_fingerprint:
        score += 0.18
        reasons.append("same_workflow_fingerprint")
        reasons_zh.append("工作流指纹一致")
    elif query.workflow_name and query.workflow_name == record.workflow_name:
        score += 0.10
        reasons.append("same_workflow_name")
        reasons_zh.append("工作流名称一致")

    outcome_weight = _OUTCOME_WEIGHT.get(record.outcome, 0.55)
    score *= 0.65 + 0.35 * outcome_weight
    score += 0.08 * record.rating
    score += 0.05 * record.confidence

    age_days = max(0.0, (_now() - record.created_at) / 86400.0)
    recency_bonus = 0.05 * math.exp(-age_days / 90.0)
    score += recency_bonus
    if recency_bonus > 0.01:
        reasons.append("recent_experience")
        reasons_zh.append("经验较新")

    if query.outcome and query.outcome != record.outcome:
        score *= 0.35
        reasons.append("outcome_filter_mismatch")
        reasons_zh.append("结果类型不匹配，已降权")

    return MemoryMatch(record=record, score=_clamp01(score), reasons=tuple(reasons), reasons_zh=tuple(reasons_zh))


def remember_experience(request: Mapping[str, Any], *, memory_path: Any, workflow_name: str = "", workflow: Optional[Mapping[str, Any]] = None) -> Json:
    """Append one experience record and return the stored entry."""

    store = ExperienceMemoryStore(memory_path)
    record = build_experience_record(request, workflow_name=workflow_name, workflow=workflow)

    # Idempotency: if the same id already exists, do not append a duplicate.
    existing = store.load()
    if any(item.id == record.id for item in existing):
        return {
            "success": True,
            "stored": False,
            "duplicate": True,
            "memory_path": str(store.memory_path),
            "record": record.to_dict(),
            "message": "Experience already exists; skipped duplicate append.",
            "message_zh": "经验已存在，已跳过重复写入。",
        }

    store.append(record)
    return {
        "success": True,
        "stored": True,
        "duplicate": False,
        "memory_path": str(store.memory_path),
        "record": record.to_dict(),
        "message": "Experience stored.",
        "message_zh": "经验已记录。",
    }


def search_memory(request: Mapping[str, Any], *, memory_path: Any) -> Json:
    """Search reusable experiences with deterministic lightweight scoring."""

    store = ExperienceMemoryStore(memory_path)
    query = MemorySearchQuery.from_request(request)
    records = store.load(max_records=request.get("max_records") if isinstance(request.get("max_records"), int) else None)
    matches = [score_record(record, query) for record in records]
    matches = [m for m in matches if m.score >= query.min_score]
    matches.sort(key=lambda item: (-item.score, -item.record.created_at, item.record.id))
    limited = tuple(matches[: query.limit])
    return {
        "success": True,
        "memory_path": str(store.memory_path),
        "query": query.to_dict(),
        "matches": [m.to_dict(include_record=query.include_records) for m in limited],
        "total_records": len(records),
        "returned": len(limited),
        "message": "Memory search completed.",
        "message_zh": "经验检索完成。",
    }


def memory_stats(*, memory_path: Any) -> Json:
    """Return memory store statistics."""

    return ExperienceMemoryStore(memory_path).stats()


def memory_context_for_request(request: Mapping[str, Any], *, memory_path: Any, workflow_name: str = "", workflow: Optional[Mapping[str, Any]] = None) -> Json:
    """Convenience helper for pipeline integration.

    This only retrieves experience. It does not automatically change decisions.
    """

    enriched: Json = dict(request)
    if workflow_name and "workflow_name" not in enriched:
        enriched["workflow_name"] = workflow_name
    if workflow is not None and "workflow_fingerprint" not in enriched:
        enriched["workflow_fingerprint"] = _workflow_fingerprint(workflow)
    result = search_memory(enriched, memory_path=memory_path)
    result["usage_note"] = "Memory is advisory evidence only; it must not bypass Reasoner, Decision, or dry-run gates."
    result["usage_note_zh"] = "经验记忆只是参考证据，不能绕过 Reasoner、Decision 或 dry-run 闸门。"
    return result


__all__ = [
    "ExperienceMemoryStore",
    "ExperienceRecord",
    "MemoryMatch",
    "MemorySearchQuery",
    "build_experience_record",
    "default_memory_path",
    "memory_context_for_request",
    "memory_stats",
    "remember_experience",
    "search_memory",
    "score_record",
]
