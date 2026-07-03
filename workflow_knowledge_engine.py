#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_knowledge_engine.py

v13 Knowledge YAML Engine for the ComfyUI Agent framework.

English -> 中文对应：
- Knowledge Engine -> 知识引擎：加载、索引、查询可维护的知识文件。
- Knowledge Pack -> 知识包：一组 YAML/JSON 知识文件。
- Evidence -> 证据：某条建议背后的知识依据。
- Query -> 查询：Reasoner / Decision / Planner 向知识层提出的问题。

Design boundary / 设计边界：
This module is deterministic and side-effect free. It does not call ComfyUI,
does not mutate workflow JSON, and does not make decisions. It only turns
maintainable YAML/JSON knowledge into structured, queryable evidence.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:  # PyYAML is normally available; JSON fallback remains supported.
    import yaml  # type: ignore
except Exception:  # pragma: no cover - depends on environment
    yaml = None

Json = Dict[str, Any]


def clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def _norm(text: Any) -> str:
    return str(text or "").strip().lower().replace("_", "-").replace(" ", "-")


def _tokens(text: Any) -> Tuple[str, ...]:
    raw = str(text or "").lower()
    seps = ["_", "-", ".", "/", "\\", ":", ";", ",", "，", "。", "、", "|", "(", ")", "[", "]"]
    for sep in seps:
        raw = raw.replace(sep, " ")
    return tuple(part for part in raw.split() if part)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_str_tuple(value: Any) -> Tuple[str, ...]:
    return tuple(str(item) for item in _as_list(value) if str(item).strip())


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_data_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is not None:
        data = yaml.safe_load(text)
        return data if data is not None else {}
    # JSON-compatible YAML fallback. This intentionally fails loudly if the
    # environment lacks PyYAML and the file is not JSON-compatible.
    return json.loads(text)


@dataclass(frozen=True)
class KnowledgeSource:
    """Where one knowledge entry came from."""

    path: str
    pack: str = "core"
    priority: int = 50

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeEntry:
    """One normalized knowledge record.

    category: broad area, e.g. sampler / cfg / diagnosis / style.
    key: stable entry key inside its category.
    targets: semantic targets affected by this entry, e.g. sampler.steps.
    goals: user goals/problems this entry helps with, e.g. sharper / faster.
    effects: qualitative effect table, kept as data for downstream modules.
    recommendations: optional candidate ops or planner hints.
    """

    id: str
    category: str
    key: str
    title: str = ""
    title_zh: str = ""
    description: str = ""
    description_zh: str = ""
    aliases: Tuple[str, ...] = ()
    targets: Tuple[str, ...] = ()
    goals: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()
    effects: Json = field(default_factory=dict)
    recommendations: Tuple[Json, ...] = ()
    constraints: Tuple[str, ...] = ()
    risk: float = 0.3
    cost: float = 0.3
    confidence: float = 0.6
    source: KnowledgeSource = field(default_factory=lambda: KnowledgeSource(path=""))

    def searchable_text(self) -> str:
        parts: List[str] = [
            self.id,
            self.category,
            self.key,
            self.title,
            self.title_zh,
            self.description,
            self.description_zh,
            *self.aliases,
            *self.targets,
            *self.goals,
            *self.capabilities,
        ]
        return " ".join(str(part) for part in parts if part)

    def to_dict(self) -> Json:
        data = asdict(self)
        data["source"] = self.source.to_dict()
        return data


@dataclass(frozen=True)
class KnowledgeMatch:
    """One entry matched for a query."""

    entry: KnowledgeEntry
    score: float
    reasons: Tuple[str, ...] = ()
    reasons_zh: Tuple[str, ...] = ()

    def to_dict(self, *, include_entry: bool = True) -> Json:
        data: Json = {
            "id": self.entry.id,
            "category": self.entry.category,
            "key": self.entry.key,
            "score": self.score,
            "reasons": list(self.reasons),
            "reasons_zh": list(self.reasons_zh),
            "target": self.entry.targets[0] if self.entry.targets else "",
            "goals": list(self.entry.goals),
            "confidence": self.entry.confidence,
            "risk": self.entry.risk,
            "cost": self.entry.cost,
        }
        if include_entry:
            data["entry"] = self.entry.to_dict()
        return data


@dataclass(frozen=True)
class KnowledgeQuery:
    """Normalized query request."""

    text: str = ""
    goal: str = ""
    target: str = ""
    category: str = ""
    capability: str = ""
    limit: int = 8
    min_score: float = 0.1
    include_entries: bool = True

    @classmethod
    def from_request(cls, request: Mapping[str, Any]) -> "KnowledgeQuery":
        def s(name: str) -> str:
            return str(request.get(name) or "").strip()

        try:
            limit = max(1, min(50, int(request.get("limit", 8))))
        except (TypeError, ValueError):
            limit = 8
        try:
            min_score = clamp01(float(request.get("min_score", 0.1)))
        except (TypeError, ValueError):
            min_score = 0.1
        include_entries = bool(request.get("include_entries", True))
        return cls(
            text=s("text") or s("problem") or s("instruction") or s("query"),
            goal=s("goal") or s("normalized_goal"),
            target=s("target"),
            category=s("category"),
            capability=s("capability"),
            limit=limit,
            min_score=min_score,
            include_entries=include_entries,
        )

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeQueryResult:
    """Structured knowledge query result."""

    success: bool
    query: KnowledgeQuery
    matches: Tuple[KnowledgeMatch, ...]
    total_entries: int
    packs: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Json:
        return {
            "success": self.success,
            "query": self.query.to_dict(),
            "matches": [m.to_dict(include_entry=self.query.include_entries) for m in self.matches],
            "total_matches": len(self.matches),
            "total_entries": self.total_entries,
            "packs": list(self.packs),
            "warnings": list(self.warnings),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class KnowledgeManifest:
    """Manifest of a loaded Knowledge Engine."""

    success: bool
    root: str
    total_entries: int
    categories: Json
    packs: Json
    files: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Json:
        return asdict(self)


class KnowledgeEngine:
    """Load and query YAML/JSON knowledge files.

    The engine is intentionally simple and deterministic. It is not a rule
    engine and does not decide what to do. It returns evidence that other
    modules can use.
    """

    def __init__(self, roots: Optional[Sequence[Path | str]] = None) -> None:
        self.roots: Tuple[Path, ...] = tuple(Path(p) for p in (roots or [default_knowledge_root()]))
        self.entries: Tuple[KnowledgeEntry, ...] = ()
        self.files: Tuple[str, ...] = ()
        self.warnings: Tuple[str, ...] = ()
        self._loaded = False

    def load(self, *, force: bool = False) -> "KnowledgeEngine":
        if self._loaded and not force:
            return self

        entries: List[KnowledgeEntry] = []
        files: List[str] = []
        warnings: List[str] = []
        seen: set[str] = set()

        for root in self.roots:
            if not root.exists():
                warnings.append(f"Knowledge root does not exist: {root}")
                continue
            for path in sorted(root.rglob("*")):
                if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
                    continue
                try:
                    data = _read_data_file(path)
                    file_entries = self._parse_file(path, data)
                    for item in file_entries:
                        if item.id in seen:
                            warnings.append(f"Duplicate knowledge id skipped: {item.id} from {path}")
                            continue
                        seen.add(item.id)
                        entries.append(item)
                    files.append(str(path))
                except Exception as exc:
                    warnings.append(f"Failed to load {path}: {exc}")

        self.entries = tuple(entries)
        self.files = tuple(files)
        self.warnings = tuple(warnings)
        self._loaded = True
        return self

    def _parse_file(self, path: Path, data: Any) -> List[KnowledgeEntry]:
        if not isinstance(data, Mapping):
            return []
        pack = str(data.get("pack") or "core")
        priority = int(_safe_float(data.get("priority"), 50))
        category_default = str(data.get("category") or path.stem)
        source = KnowledgeSource(path=str(path), pack=pack, priority=priority)
        raw_entries = data.get("entries")
        if isinstance(raw_entries, Mapping):
            iterable = []
            for key, value in raw_entries.items():
                if isinstance(value, Mapping):
                    merged = dict(value)
                    merged.setdefault("key", key)
                    iterable.append(merged)
        else:
            iterable = [item for item in _as_list(raw_entries) if isinstance(item, Mapping)]

        parsed: List[KnowledgeEntry] = []
        for raw in iterable:
            key = str(raw.get("key") or raw.get("id") or "").strip()
            if not key:
                continue
            category = str(raw.get("category") or category_default).strip()
            entry_id = str(raw.get("id") or f"{category}.{key}").strip()
            recommendations = tuple(dict(item) for item in _as_list(raw.get("recommendations")) if isinstance(item, Mapping))
            parsed.append(
                KnowledgeEntry(
                    id=entry_id,
                    category=category,
                    key=key,
                    title=str(raw.get("title") or ""),
                    title_zh=str(raw.get("title_zh") or raw.get("zh") or ""),
                    description=str(raw.get("description") or ""),
                    description_zh=str(raw.get("description_zh") or ""),
                    aliases=_as_str_tuple(raw.get("aliases")),
                    targets=_as_str_tuple(raw.get("targets") or raw.get("target")),
                    goals=_as_str_tuple(raw.get("goals") or raw.get("goal")),
                    capabilities=_as_str_tuple(raw.get("capabilities") or raw.get("capability")),
                    effects=dict(raw.get("effects") or {}) if isinstance(raw.get("effects"), Mapping) else {},
                    recommendations=recommendations,
                    constraints=_as_str_tuple(raw.get("constraints")),
                    risk=clamp01(_safe_float(raw.get("risk"), 0.3)),
                    cost=clamp01(_safe_float(raw.get("cost"), 0.3)),
                    confidence=clamp01(_safe_float(raw.get("confidence"), 0.6)),
                    source=source,
                )
            )
        return parsed

    def manifest(self) -> KnowledgeManifest:
        self.load()
        categories: Json = {}
        packs: Json = {}
        for entry in self.entries:
            categories[entry.category] = categories.get(entry.category, 0) + 1
            packs[entry.source.pack] = packs.get(entry.source.pack, 0) + 1
        return KnowledgeManifest(
            success=True,
            root=";".join(str(root) for root in self.roots),
            total_entries=len(self.entries),
            categories=dict(sorted(categories.items())),
            packs=dict(sorted(packs.items())),
            files=self.files,
            warnings=self.warnings,
        )

    def query(self, query: KnowledgeQuery | Mapping[str, Any]) -> KnowledgeQueryResult:
        self.load()
        q = query if isinstance(query, KnowledgeQuery) else KnowledgeQuery.from_request(query)
        matches: List[KnowledgeMatch] = []
        for entry in self.entries:
            score, reasons, reasons_zh = self._score(entry, q)
            if score >= q.min_score:
                matches.append(KnowledgeMatch(entry=entry, score=score, reasons=tuple(reasons), reasons_zh=tuple(reasons_zh)))
        matches.sort(key=lambda item: (item.score, item.entry.confidence, -item.entry.risk, -item.entry.cost), reverse=True)
        return KnowledgeQueryResult(
            success=True,
            query=q,
            matches=tuple(matches[: q.limit]),
            total_entries=len(self.entries),
            packs=tuple(sorted({entry.source.pack for entry in self.entries})),
            warnings=self.warnings,
        )

    def _score(self, entry: KnowledgeEntry, query: KnowledgeQuery) -> Tuple[float, List[str], List[str]]:
        score = 0.0
        reasons: List[str] = []
        reasons_zh: List[str] = []

        if query.category and _norm(query.category) == _norm(entry.category):
            score += 0.22
            reasons.append("category match")
            reasons_zh.append("类别匹配")

        if query.target:
            target_norm = _norm(query.target)
            target_norms = {_norm(item) for item in entry.targets}
            if target_norm in target_norms:
                score += 0.45
                reasons.append("target exact match")
                reasons_zh.append("目标字段精确匹配")
            elif any(target_norm in item or item in target_norm for item in target_norms):
                score += 0.28
                reasons.append("target partial match")
                reasons_zh.append("目标字段部分匹配")

        if query.goal:
            goal_norm = _norm(query.goal)
            goal_norms = {_norm(item) for item in entry.goals}
            if goal_norm in goal_norms:
                score += 0.38
                reasons.append("goal match")
                reasons_zh.append("目标/问题匹配")
            elif any(goal_norm in item or item in goal_norm for item in goal_norms):
                score += 0.22
                reasons.append("goal partial match")
                reasons_zh.append("目标/问题部分匹配")

        if query.capability:
            cap_norm = _norm(query.capability)
            cap_norms = {_norm(item) for item in entry.capabilities}
            if cap_norm in cap_norms:
                score += 0.25
                reasons.append("capability match")
                reasons_zh.append("能力匹配")

        if query.text:
            text_tokens = set(_tokens(query.text))
            entry_tokens = set(_tokens(entry.searchable_text()))
            overlap = text_tokens & entry_tokens
            if overlap:
                score += min(0.35, 0.08 * len(overlap))
                reasons.append("text overlap: " + ", ".join(sorted(overlap)[:6]))
                reasons_zh.append("文本关键词重合")
            # Chinese / compact aliases may not tokenize well; do direct checks.
            lowered = query.text.lower()
            direct_hits = [alias for alias in entry.aliases if alias and alias.lower() in lowered]
            if direct_hits:
                score += min(0.30, 0.12 * len(direct_hits))
                reasons.append("alias direct hit")
                reasons_zh.append("别名直接命中")

        # Knowledge confidence and pack priority should refine, not dominate.
        score += 0.10 * entry.confidence
        score += min(0.08, max(0.0, entry.source.priority) / 1000.0)
        return clamp01(score), reasons, reasons_zh


def default_knowledge_root() -> Path:
    env = os.environ.get("COMFY_AGENT_KNOWLEDGE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "knowledge"


_ENGINE_CACHE: Dict[Tuple[str, ...], KnowledgeEngine] = {}


def load_knowledge_engine(roots: Optional[Sequence[str | Path]] = None, *, force: bool = False) -> KnowledgeEngine:
    root_tuple = tuple(str(Path(p)) for p in (roots or [default_knowledge_root()]))
    if force or root_tuple not in _ENGINE_CACHE:
        _ENGINE_CACHE[root_tuple] = KnowledgeEngine([Path(p) for p in root_tuple]).load(force=True)
    return _ENGINE_CACHE[root_tuple]


def knowledge_manifest(request: Optional[Mapping[str, Any]] = None) -> Json:
    roots = _roots_from_request(request or {})
    return load_knowledge_engine(roots).manifest().to_dict()


def query_knowledge(request: Mapping[str, Any]) -> Json:
    roots = _roots_from_request(request)
    return load_knowledge_engine(roots).query(request).to_dict()


def explain_target(target: str, *, limit: int = 5) -> Json:
    return query_knowledge({"target": target, "limit": limit})


def suggest_for_problem(problem: str, *, limit: int = 8) -> Json:
    return query_knowledge({"text": problem, "goal": problem, "limit": limit})


def _roots_from_request(request: Mapping[str, Any]) -> Optional[List[str]]:
    value = request.get("knowledge_roots") or request.get("knowledge_root") or request.get("knowledge_path")
    if not value:
        return None
    return [str(item) for item in _as_list(value) if str(item).strip()]


__all__ = [
    "KnowledgeEngine",
    "KnowledgeEntry",
    "KnowledgeManifest",
    "KnowledgeMatch",
    "KnowledgeQuery",
    "KnowledgeQueryResult",
    "KnowledgeSource",
    "default_knowledge_root",
    "explain_target",
    "knowledge_manifest",
    "load_knowledge_engine",
    "query_knowledge",
    "suggest_for_problem",
]
