#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate planner candidates before they become selected plans."""

from __future__ import annotations

from typing import Iterable, List, Mapping, Sequence

from .models import PlanAction, PlanValidationIssue

SAFE_SET_TARGET_PREFIXES = (
    "sampler.",
    "latent_source.",
    "positive_prompt.",
    "negative_prompt.",
    "output.",
    "vae.",
    "checkpoint.",
)


def validate_actions(actions: Sequence[PlanAction], *, allow_text_append_ops: bool = False) -> List[PlanValidationIssue]:
    issues: List[PlanValidationIssue] = []
    seen = set()
    for action in actions:
        if not action.target:
            issues.append(PlanValidationIssue("error", "Action target is empty.", "操作目标为空。"))
            continue
        if action.target in seen:
            issues.append(PlanValidationIssue("warning", f"Duplicate target: {action.target}", "重复修改同一目标。", target=action.target))
        seen.add(action.target)
        if action.operation == "set":
            if not action.target.startswith(SAFE_SET_TARGET_PREFIXES):
                issues.append(PlanValidationIssue("warning", f"Target is not in known safe semantic namespace: {action.target}", "目标不在已知安全语义命名空间中。", target=action.target))
        elif action.operation == "append_text":
            if not allow_text_append_ops:
                issues.append(PlanValidationIssue("warning", "append_text is not executable by all workflow_ops versions; keep it as advisory unless supported.", "append_text 不是所有 workflow_ops 版本都支持，建议先作为建议操作。", target=action.target))
        else:
            issues.append(PlanValidationIssue("error", f"Unsupported action operation: {action.operation}", "不支持的规划操作。", target=action.target))

        if action.target.endswith("width") or action.target.endswith("height"):
            try:
                value = int(action.value)
                if value < 64 or value > 4096:
                    issues.append(PlanValidationIssue("error", f"Resolution value out of safe range: {value}", "分辨率数值超出安全范围。", target=action.target))
                elif value % 8 != 0:
                    issues.append(PlanValidationIssue("warning", f"Resolution value is not a multiple of 8: {value}", "分辨率最好是 8 的倍数。", target=action.target))
            except Exception:
                issues.append(PlanValidationIssue("error", "Resolution value must be an integer.", "分辨率必须是整数。", target=action.target))

        if action.target == "sampler.steps":
            try:
                value = int(action.value)
                if value < 1 or value > 300:
                    issues.append(PlanValidationIssue("error", f"steps out of safe range: {value}", "steps 超出安全范围。", target=action.target))
            except Exception:
                issues.append(PlanValidationIssue("error", "steps must be an integer.", "steps 必须是整数。", target=action.target))

        if action.target == "sampler.cfg":
            try:
                value = float(action.value)
                if value < 0 or value > 50:
                    issues.append(PlanValidationIssue("error", f"cfg out of safe range: {value}", "cfg 超出安全范围。", target=action.target))
            except Exception:
                issues.append(PlanValidationIssue("error", "cfg must be numeric.", "cfg 必须是数字。", target=action.target))
    return issues


def has_errors(issues: Iterable[PlanValidationIssue]) -> bool:
    return any(issue.level == "error" for issue in issues)
