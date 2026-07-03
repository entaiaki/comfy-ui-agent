#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Problem normalization for reasoning."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Tuple

from .models import ProblemSpec, clamp01

try:
    from workflow_knowledge import normalize_goal
except Exception:  # pragma: no cover - fallback for isolated tests
    def normalize_goal(goal: str) -> str:
        return str(goal or "").strip().lower().replace(" ", "_")


def _detect_language(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[a-zA-Z]", text):
        return "en"
    return "unknown"


def _labels_for_goal(goal: str, text: str) -> Tuple[str, ...]:
    low = (text or "").lower()
    labels = set()
    if goal in {"sharper", "fix_bad_anatomy", "more_natural"}:
        labels.add("quality")
    if goal in {"faster"}:
        labels.add("performance")
    if goal in {"anime_style", "photorealistic"}:
        labels.add("style")
    if any(x in low for x in ("不要", "别", "avoid", "without", "not")):
        labels.add("constraint")
    return tuple(sorted(labels))


def normalize_problem(request: Mapping[str, Any]) -> ProblemSpec:
    raw = (
        request.get("problem")
        or request.get("goal")
        or request.get("intent")
        or request.get("text")
        or request.get("message")
        or request.get("user_request")
        or ""
    )
    if isinstance(raw, Mapping):
        raw_text = str(raw.get("name") or raw.get("goal") or raw.get("problem") or "")
    else:
        raw_text = str(raw or "")
    raw_text = raw_text.strip()
    normalized = normalize_goal(raw_text)
    confidence = 0.75 if normalized and normalized != raw_text else 0.45
    if raw_text and normalized in {"sharper", "faster", "anime_style", "photorealistic", "fix_bad_anatomy", "more_prompt_adherence", "more_natural"}:
        confidence = 0.88
    if not raw_text:
        normalized = "unknown"
        confidence = 0.0
    return ProblemSpec(
        raw_text=raw_text,
        normalized_goal=normalized,
        labels=_labels_for_goal(normalized, raw_text),
        language=_detect_language(raw_text),
        confidence=clamp01(confidence),
    )
