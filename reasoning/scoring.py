#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hypothesis scoring."""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Tuple

from .models import Evidence, Hypothesis, ScoredHypothesis, clamp01


def score_hypotheses(hypotheses: Iterable[Hypothesis], observations: Mapping[str, Any], evidence: Iterable[Evidence]) -> List[ScoredHypothesis]:
    evidence_list = list(evidence)
    has_unknown_nodes = float(observations.get("count.unknown_nodes") or 0) > 0
    multiple_samplers = float(observations.get("count.samplers") or 0) > 1
    pipeline = str(observations.get("pipeline") or "unknown")

    scored: List[ScoredHypothesis] = []
    for h in hypotheses:
        score = h.base_score
        confidence = h.confidence
        cost = h.cost
        risk = h.risk
        reasons: List[str] = []

        if h.target and h.target in observations:
            score += 0.05
            reasons.append(f"target {h.target} exists in current workflow")

        if h.target.startswith("sampler.") and multiple_samplers:
            risk += 0.18
            confidence -= 0.08
            reasons.append("multiple samplers make sampler target less certain")

        if "graph_edit" in h.tags and has_unknown_nodes:
            risk += 0.15
            score -= 0.08
            reasons.append("unknown/custom nodes make graph edits riskier")

        if pipeline == "flux" and h.target == "sampler.cfg":
            score -= 0.10
            confidence -= 0.10
            reasons.append("Flux-like workflow may use separate guidance instead of classic CFG")

        if h.action == "ask":
            if h.goal == "unknown":
                score += 0.25
                confidence += 0.2
            reasons.append("clarification is safer for unsupported goals")

        # Evidence support: goal advice present increases confidence slightly.
        if any(ev.id == "knowledge.goal_advice" for ev in evidence_list):
            confidence += 0.08
            reasons.append("knowledge layer has advice for this goal")
        if any(ev.kind.value == "warning" for ev in evidence_list):
            risk += 0.04

        # Prefer low cost and low risk for early automatic reasoning.
        final = score + (confidence * 0.25) - (cost * 0.15) - (risk * 0.25)
        scored.append(ScoredHypothesis(
            hypothesis=h,
            score=clamp01(final),
            confidence=clamp01(confidence),
            cost=clamp01(cost),
            risk=clamp01(risk),
            reasons=tuple(reasons),
        ))

    scored.sort(key=lambda item: (-item.score, item.risk, item.cost, item.hypothesis.id))
    return scored
