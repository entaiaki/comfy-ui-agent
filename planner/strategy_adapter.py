#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert reasoner strategy or structured intents into planner actions."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .models import PlanAction

Json = Dict[str, Any]


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _current_value(semantics: Mapping[str, Any], target: str, default: Any = None) -> Any:
    # workflow_semantics full output usually has nodes + main. Keep this soft so
    # planner never breaks if semantics shape changes.
    nodes = semantics.get("nodes") if isinstance(semantics, Mapping) else None
    main = semantics.get("main", {}) if isinstance(semantics, Mapping) else {}
    role = target.split(".", 1)[0]
    field = target.split(".", 1)[1] if "." in target else ""
    node_id = main.get(role) if isinstance(main, Mapping) else None
    if isinstance(nodes, Mapping) and node_id and str(node_id) in nodes:
        node = nodes[str(node_id)]
        if isinstance(node, Mapping):
            inputs = node.get("inputs")
            if isinstance(inputs, Mapping) and field in inputs:
                return inputs[field]
    return default


def actions_from_strategy(strategy_items: Iterable[Any], semantics: Mapping[str, Any]) -> List[PlanAction]:
    actions: List[PlanAction] = []
    for index, raw in enumerate(strategy_items):
        item = raw.to_dict() if hasattr(raw, "to_dict") else raw
        if not isinstance(item, Mapping):
            continue
        target = str(item.get("target") or "").strip()
        action = str(item.get("action") or "set").strip()
        value = item.get("value")
        if not target:
            continue
        operation = "set"
        tags: List[str] = []
        if action in {"append_style_tokens", "append_negative_tokens"}:
            operation = "append_text"
            tags.append("style")
        elif action in {"ask", "consider_add"}:
            # Advisory only; planner 2.0 does not auto-create graph nodes yet.
            continue
        elif value is None:
            current = _current_value(semantics, target)
            if target == "sampler.steps" and action == "increase":
                value = max(28, int(round(_num(current, 20) + 6)))
            elif target == "sampler.steps" and action == "decrease":
                value = max(8, int(round(_num(current, 20) * 0.7)))
            elif target == "sampler.cfg" and action in {"slightly_increase", "increase"}:
                value = round(min(_num(current, 7.0) + 0.75, 12.0), 2)
            elif target == "sampler.cfg" and action in {"slightly_decrease", "decrease"}:
                value = round(max(_num(current, 7.0) - 0.75, 1.0), 2)
        if value is None:
            continue
        actions.append(PlanAction(
            id=f"reasoner_action.{index + 1}",
            target=target,
            operation=operation,
            value=value,
            reason=str(item.get("reason") or item.get("expected_effect") or "Imported from reasoner strategy."),
            reason_zh=str(item.get("reason_zh") or item.get("expected_effect_zh") or "来自推理器策略。"),
            source="reasoner",
            confidence=float(item.get("confidence") or 0.65),
            tags=tuple(tags),
        ))
    return actions


def actions_from_structured_request(request: Mapping[str, Any]) -> List[PlanAction]:
    intent = str(request.get("intent") or request.get("action") or "").strip().lower().replace("-", "_")
    actions: List[PlanAction] = []
    if intent in {"set_size", "size", "resolution", "set_resolution"}:
        width = request.get("width") or request.get("w")
        height = request.get("height") or request.get("h")
        if width is not None and height is not None:
            actions.extend([
                PlanAction("intent.size.width", "latent_source.width", "set", int(width), "Set requested width.", "设置用户要求的宽度。", source="intent", confidence=0.95),
                PlanAction("intent.size.height", "latent_source.height", "set", int(height), "Set requested height.", "设置用户要求的高度。", source="intent", confidence=0.95),
            ])
    elif intent in {"set_sampler_steps", "set_steps", "steps"}:
        value = request.get("steps") or request.get("value")
        if value is not None:
            actions.append(PlanAction("intent.steps", "sampler.steps", "set", int(value), "Set requested sampler steps.", "设置用户要求的采样步数。", source="intent", confidence=0.98))
    elif intent in {"set_cfg", "cfg", "guidance"}:
        value = request.get("cfg") or request.get("guidance") or request.get("value")
        if value is not None:
            actions.append(PlanAction("intent.cfg", "sampler.cfg", "set", float(value), "Set requested CFG/guidance.", "设置用户要求的 CFG/引导强度。", source="intent", confidence=0.92))
    elif intent in {"set_positive_prompt", "prompt", "positive_prompt", "set_prompt"}:
        value = request.get("prompt") or request.get("text") or request.get("value")
        if value is not None:
            actions.append(PlanAction("intent.positive_prompt", "positive_prompt.text", "set", str(value), "Set requested positive prompt.", "设置用户要求的正向提示词。", source="intent", confidence=0.95))
    elif intent in {"set_negative_prompt", "negative_prompt"}:
        value = request.get("negative_prompt") or request.get("negative") or request.get("value")
        if value is not None:
            actions.append(PlanAction("intent.negative_prompt", "negative_prompt.text", "set", str(value), "Set requested negative prompt.", "设置用户要求的负向提示词。", source="intent", confidence=0.95))
    return actions


def actions_from_text(text: str) -> List[PlanAction]:
    text = str(text or "")
    actions: List[PlanAction] = []
    m = re.search(r"(\d{3,4})\s*[x×*]\s*(\d{3,4})", text, flags=re.I)
    if m:
        actions.append(PlanAction("text.size.width", "latent_source.width", "set", int(m.group(1)), "Parsed width from text.", "从文本解析出宽度。", source="text", confidence=0.82))
        actions.append(PlanAction("text.size.height", "latent_source.height", "set", int(m.group(2)), "Parsed height from text.", "从文本解析出高度。", source="text", confidence=0.82))
    m = re.search(r"(?:steps?|步数|采样步数)\D{0,8}(\d{1,3})", text, flags=re.I)
    if m:
        actions.append(PlanAction("text.steps", "sampler.steps", "set", int(m.group(1)), "Parsed steps from text.", "从文本解析出采样步数。", source="text", confidence=0.82))
    m = re.search(r"(?:cfg|guidance|引导|提示词强度)\D{0,8}(\d+(?:\.\d+)?)", text, flags=re.I)
    if m:
        actions.append(PlanAction("text.cfg", "sampler.cfg", "set", float(m.group(1)), "Parsed CFG from text.", "从文本解析出 CFG。", source="text", confidence=0.78))
    return actions
