#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence collection for reasoning."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from .models import Evidence, EvidenceKind, Observation, ProblemSpec, clamp01

try:
    from workflow_knowledge import get_goal_advice, get_knowledge_card, risk_check_target_value
except Exception:  # pragma: no cover
    get_goal_advice = None
    get_knowledge_card = None
    risk_check_target_value = None


def collect_evidence(problem: ProblemSpec, observations: Iterable[Observation]) -> List[Evidence]:
    evidence: List[Evidence] = []
    evidence.append(Evidence(
        id="user.problem",
        kind=EvidenceKind.USER,
        summary=f"User requested: {problem.raw_text or problem.normalized_goal}",
        summary_zh=f"用户目标：{problem.raw_text or problem.normalized_goal}",
        weight=problem.confidence,
        source="request",
        data=problem.to_dict(),
    ))

    if get_goal_advice is not None:
        try:
            advice = get_goal_advice(problem.normalized_goal)
            if advice.get("found"):
                evidence.append(Evidence(
                    id="knowledge.goal_advice",
                    kind=EvidenceKind.KNOWLEDGE,
                    summary=str(advice.get("summary") or "Knowledge has advice for this goal."),
                    summary_zh=str(advice.get("summary_zh") or "知识库有该目标的建议。"),
                    weight=0.8,
                    source="workflow_knowledge.get_goal_advice",
                    data=advice,
                ))
            else:
                evidence.append(Evidence(
                    id="knowledge.goal_missing",
                    kind=EvidenceKind.WARNING,
                    summary="No deterministic advice is available for this goal.",
                    summary_zh="知识库暂时没有该目标的稳定建议。",
                    weight=0.4,
                    source="workflow_knowledge.get_goal_advice",
                    data=advice,
                ))
        except Exception as exc:
            evidence.append(Evidence(
                id="knowledge.goal_error",
                kind=EvidenceKind.WARNING,
                summary=f"Knowledge goal lookup failed: {exc}",
                summary_zh=f"知识库目标查询失败：{exc}",
                weight=0.2,
                source="workflow_knowledge.get_goal_advice",
            ))

    for obs in observations:
        if obs.key.startswith("sampler.") or obs.key.startswith("latent_source."):
            target = obs.key
            if get_knowledge_card is not None:
                try:
                    card = get_knowledge_card(target)
                except Exception:
                    card = None
                if isinstance(card, Mapping):
                    evidence.append(Evidence(
                        id=f"knowledge.{target}",
                        kind=EvidenceKind.KNOWLEDGE,
                        summary=str(card.get("summary") or f"Knowledge card for {target}."),
                        summary_zh=str(card.get("summary_zh") or f"{target} 的知识卡片。"),
                        weight=0.65,
                        source="workflow_knowledge.get_knowledge_card",
                        target=target,
                        data=dict(card),
                    ))
            evidence.append(Evidence(
                id=f"workflow.{target}",
                kind=EvidenceKind.WORKFLOW,
                summary=f"Current {target} is {obs.value}.",
                summary_zh=f"当前 {target} = {obs.value}。",
                weight=0.7,
                source="semantic_observer",
                target=target,
                data=obs.to_dict(),
            ))

    for obs in observations:
        if obs.key.startswith("warning."):
            evidence.append(Evidence(
                id=f"workflow.{obs.key}",
                kind=EvidenceKind.WARNING,
                summary=obs.note or f"Workflow warning: {obs.key}",
                summary_zh=obs.note or f"工作流警告：{obs.key}",
                weight=0.6,
                source="semantic_observer",
                data=obs.to_dict(),
            ))
    return evidence
