#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cost model for planner candidates.

The numbers are intentionally conservative and normalized to 0..1. They are not
scientific measurements; they are deterministic planning heuristics that keep
automatic edits small first.
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

from .models import PlanAction, PlanCost, clamp01, average


TARGET_COSTS = {
    "sampler.steps": (0.22, 0.10, 0.08),
    "sampler.cfg": (0.08, 0.08, 0.12),
    "sampler.seed": (0.02, 0.05, 0.10),
    "sampler.sampler_name": (0.18, 0.20, 0.22),
    "sampler.scheduler": (0.16, 0.18, 0.20),
    "latent_source.width": (0.35, 0.16, 0.18),
    "latent_source.height": (0.35, 0.16, 0.18),
    "latent_source.batch_size": (0.30, 0.12, 0.20),
    "positive_prompt.text": (0.02, 0.08, 0.10),
    "negative_prompt.text": (0.02, 0.08, 0.10),
    "checkpoint.ckpt_name": (0.55, 0.70, 0.60),
    "vae.vae_name": (0.28, 0.40, 0.35),
    "output.filename_prefix": (0.00, 0.02, 0.01),
}

DEFAULT_COST = (0.20, 0.30, 0.35)


def estimate_action_cost(action: PlanAction) -> PlanCost:
    compute, edit, risk = TARGET_COSTS.get(action.target, DEFAULT_COST)
    reasons: List[str] = []
    if action.operation != "set":
        edit += 0.06
        risk += 0.08
        reasons.append(f"operation {action.operation!r} is less directly supported than set")
    if "graph_edit" in action.tags:
        edit += 0.25
        risk += 0.25
        reasons.append("graph editing is intentionally treated as higher risk")
    if "style" in action.tags:
        risk += 0.05
        reasons.append("style edits can change artistic intent")
    total = clamp01((compute * 0.30) + (edit * 0.30) + (risk * 0.35) + ((1.0 - action.confidence) * 0.05))
    return PlanCost(
        compute_cost=clamp01(compute),
        edit_cost=clamp01(edit),
        risk_cost=clamp01(risk),
        user_confirmation_cost=0.0,
        total=total,
        reasons=tuple(reasons),
    )


def combine_costs(actions: Iterable[PlanAction]) -> PlanCost:
    items = [estimate_action_cost(a) for a in actions]
    if not items:
        return PlanCost(total=0.0, reasons=("no actions",))
    reasons: List[str] = []
    for item in items:
        reasons.extend(item.reasons)
    compute = clamp01(sum(i.compute_cost for i in items))
    edit = clamp01(sum(i.edit_cost for i in items))
    risk = clamp01(max(i.risk_cost for i in items))
    confirmation = 0.20 if risk >= 0.45 or edit >= 0.65 else 0.0
    if confirmation:
        reasons.append("plan contains medium/high risk edits; confirmation is recommended")
    total = clamp01((compute * 0.25) + (edit * 0.25) + (risk * 0.35) + (confirmation * 0.15))
    return PlanCost(
        compute_cost=compute,
        edit_cost=edit,
        risk_cost=risk,
        user_confirmation_cost=confirmation,
        total=total,
        reasons=tuple(dict.fromkeys(reasons)),
    )
