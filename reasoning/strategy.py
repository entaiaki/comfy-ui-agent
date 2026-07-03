#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strategy generation from scored hypotheses."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .models import ScoredHypothesis, StrategyStep, clamp01


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_like(value: float) -> int:
    return int(round(value))


def _strategy_value(target: str, action: str, observations: Mapping[str, Any], goal: str) -> Any:
    if target == "sampler.steps":
        current = _num(observations.get("sampler.steps"), 20)
        if action == "increase":
            return max(_int_like(current + 6), 28 if current < 28 else _int_like(current + 4))
        if action == "decrease":
            return max(8, _int_like(current * 0.7))
    if target == "sampler.cfg":
        current = _num(observations.get("sampler.cfg"), 7)
        if action == "slightly_increase":
            return round(min(current + 0.75, 12), 2)
        if action == "slightly_decrease":
            return round(max(current - 0.75, 1), 2)
    if target == "positive_prompt.text" and action == "append_style_tokens":
        if goal == "anime_style":
            return "anime style, clean line art, vibrant illustration, detailed character design"
        if goal == "photorealistic":
            return "photorealistic, natural skin texture, realistic lighting, professional photography"
    if target == "negative_prompt.text" and action == "append_negative_tokens":
        return "bad anatomy, deformed hands, extra fingers, missing fingers, distorted limbs"
    return None


def build_strategy(scored: Iterable[ScoredHypothesis], observations: Mapping[str, Any], *, max_steps: int = 3) -> List[StrategyStep]:
    steps: List[StrategyStep] = []
    used_targets = set()
    for index, item in enumerate(scored):
        h = item.hypothesis
        if h.action in {"ask", "consider_add"}:
            # Keep as strategy advice, but not as automatic op.
            value = None
        else:
            value = _strategy_value(h.target, h.action, observations, h.goal)
        if h.target and h.target in used_targets:
            continue
        used_targets.add(h.target)
        step = StrategyStep(
            id=f"strategy.{len(steps)+1}",
            target=h.target,
            action=h.action,
            value=value,
            priority=len(steps) + 1,
            confidence=item.confidence,
            reason=h.rationale,
            reason_zh=h.rationale_zh,
            expected_effect=h.title,
            expected_effect_zh=h.title_zh,
            risk="high" if item.risk >= 0.65 else "medium" if item.risk >= 0.35 else "low",
            cost="high" if item.cost >= 0.65 else "medium" if item.cost >= 0.35 else "low",
        )
        steps.append(step)
        if len(steps) >= max_steps:
            break
    return steps
