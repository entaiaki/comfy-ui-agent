#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory.search

Deterministic retrieval for Agent Memory.

This is not a vector search engine. It is a safe first layer that uses exact and
lightweight token overlap. The API is deliberately compatible with future vector
retrieval: callers receive MemoryMatch objects with scores and reasons.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, List, Sequence, Tuple

from .models import ExperienceEntry, MemoryMatch, MemoryQuery

_WORD_RE = re.compile(r"[A-Za-z0-9_\-\.]+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> Tuple[str, ...]:
    if not text:
        return ()
    return tuple(tok.lower() for tok in _WORD_RE.findall(str(text)) if tok.strip())


def entry_text(entry: ExperienceEntry) -> str:
    parts = [
        entry.workflow,
        entry.problem,
        entry.goal,
        entry.intent,
        entry.summary,
        entry.summary_zh,
        " ".join(entry.tags),
        entry.outcome.message,
        entry.outcome.message_zh,
    ]
    # Keep payload text shallow to avoid giant scores from huge JSON dumps.
    for payload in (entry.request, entry.reasoning, entry.plan, entry.metadata):
        for key in ("text", "query", "intent", "goal", "problem", "summary", "message"):
            value = payload.get(key) if isinstance(payload, dict) else None
            if value:
                parts.append(str(value))
    return "\n".join(part for part in parts if part)


def score_entry(entry: ExperienceEntry, query: MemoryQuery) -> MemoryMatch | None:
    score = 0.0
    reasons: List[str] = []

    if query.kind and entry.kind != query.kind:
        return None
    if query.outcome_status and entry.outcome.status != query.outcome_status:
        return None
    if query.workflow and query.workflow not in entry.workflow:
        return None
    if query.problem and query.problem.lower() not in entry.problem.lower():
        return None
    if query.goal and query.goal.lower() != entry.goal.lower():
        return None
    if query.intent and query.intent.lower() != entry.intent.lower():
        return None
    if query.min_score is not None:
        if entry.outcome.score is None or entry.outcome.score < query.min_score:
            return None

    entry_tags = {tag.lower() for tag in entry.tags}
    query_tags = {tag.lower() for tag in query.tags}
    if query_tags:
        if not query_tags.issubset(entry_tags):
            return None
        score += 0.25 + (0.03 * len(query_tags))
        reasons.append("matched tags")

    if query.workflow:
        score += 0.12
        reasons.append("matched workflow")
    if query.problem:
        score += 0.16
        reasons.append("matched problem")
    if query.goal:
        score += 0.20
        reasons.append("matched goal")
    if query.intent:
        score += 0.14
        reasons.append("matched intent")

    q_tokens = set(tokenize(query.text))
    if q_tokens:
        e_tokens = set(tokenize(entry_text(entry)))
        overlap = q_tokens & e_tokens
        if not overlap:
            return None
        overlap_ratio = len(overlap) / max(1, len(q_tokens))
        score += min(0.35, overlap_ratio * 0.35)
        reasons.append(f"token overlap: {', '.join(sorted(overlap)[:8])}")

    # Outcome quality nudges useful experiences upward, but never hides failures;
    # failures are valuable when diagnosing what not to do.
    if entry.outcome.status == "success":
        score += 0.08
        reasons.append("successful outcome")
    elif entry.outcome.status == "failed":
        score += 0.02
        reasons.append("failure retained as cautionary experience")
    if entry.outcome.score is not None:
        score += max(0.0, min(float(entry.outcome.score), 1.0)) * 0.08
        reasons.append("has outcome score")
    if entry.confidence is not None:
        score += max(0.0, min(float(entry.confidence), 1.0)) * 0.04
        reasons.append("has confidence")

    # Recency nudge: newest entries win ties without dominating semantics.
    score += min(0.05, max(0.0, entry.created_at / 10_000_000_000.0) * 0.05)

    if score <= 0:
        return None
    return MemoryMatch(entry=entry, score=min(score, 1.0), reasons=tuple(reasons))


def search_entries(entries: Sequence[ExperienceEntry], query: MemoryQuery) -> List[MemoryMatch]:
    matches: List[MemoryMatch] = []
    for entry in entries:
        match = score_entry(entry, query)
        if match is not None:
            matches.append(match)
    matches.sort(key=lambda item: (-item.score, -item.entry.created_at, item.entry.id))
    return matches[: query.limit]
