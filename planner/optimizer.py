#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan scoring and selection."""

from __future__ import annotations

from typing import Iterable, List

from .models import PlanCandidate, PlanPriority, PlanStatus, clamp01


def score_candidate(candidate: PlanCandidate, priority: PlanPriority) -> float:
    confidence = candidate.confidence
    cost = candidate.cost.total
    risk = candidate.cost.risk_cost
    action_bonus = min(len(candidate.actions), 3) * 0.03

    if priority == PlanPriority.SPEED:
        score = confidence - (candidate.cost.compute_cost * 0.45) - (risk * 0.20) - (candidate.cost.edit_cost * 0.15) + action_bonus
    elif priority == PlanPriority.QUALITY:
        score = confidence - (risk * 0.25) - (candidate.cost.edit_cost * 0.15) - (candidate.cost.compute_cost * 0.10) + action_bonus
    elif priority == PlanPriority.SAFETY:
        score = confidence - (risk * 0.45) - (candidate.cost.edit_cost * 0.25) - (candidate.cost.compute_cost * 0.10)
    else:
        score = confidence - (cost * 0.35) - (risk * 0.20) + action_bonus
    if candidate.status == PlanStatus.UNSAFE:
        score -= 0.5
    if candidate.status == PlanStatus.NEEDS_CONFIRMATION:
        score -= 0.08
    return clamp01(score)


def rank_candidates(candidates: Iterable[PlanCandidate], priority: PlanPriority) -> List[PlanCandidate]:
    ranked: List[PlanCandidate] = []
    for item in candidates:
        score = score_candidate(item, priority)
        ranked.append(PlanCandidate(
            id=item.id,
            title=item.title,
            title_zh=item.title_zh,
            priority=item.priority,
            actions=item.actions,
            cost=item.cost,
            score=score,
            confidence=item.confidence,
            status=item.status,
            summary=item.summary,
            summary_zh=item.summary_zh,
            tradeoffs=item.tradeoffs,
            issues=item.issues,
            trace=item.trace,
        ))
    ranked.sort(key=lambda c: (-c.score, c.cost.risk_cost, c.cost.total, c.id))
    return ranked
