#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_self_check.py

Self-check loop for ComfyUI workflow edits.

English -> 中文对应：
- Self-check -> 自检：每次改完都回头看。
- Dry run -> 试运行/预演：只应用修改并检查，不真正发给 ComfyUI 出图。
- Over-edit -> 过度修改：改动超过当前目标所需，容易走偏。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from workflow_agent_planner import plan_ops_from_text
from workflow_diff import diff_to_human, diff_workflows
from workflow_inspect import inspect_workflow
from workflow_ops import apply_ops
from workflow_validator import validate_workflow

Json = Dict[str, Any]


def _normalize_ops(ops: Optional[List[Dict[str, Any]]], text: str = "") -> List[Dict[str, Any]]:
    if ops is not None:
        if not isinstance(ops, list):
            raise ValueError("ops must be a list")
        return ops
    return plan_ops_from_text(text or "")


def _risk_hints(diff: Dict[str, Any], validation_after: Dict[str, Any], planned_ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    summary = diff.get("summary", {})

    if summary.get("added_nodes", 0) or summary.get("removed_nodes", 0):
        hints.append({
            "level": "medium",
            "kind": "graph_structure_changed",
            "message": "本次涉及新增/删除节点，属于结构性修改；需要重点确认连线和必要输入。",
        })

    if summary.get("changed_nodes", 0) > max(3, len(planned_ops) * 2):
        hints.append({
            "level": "medium",
            "kind": "possible_over_edit",
            "message": "修改节点数量明显多于操作数量，可能改多了；建议人工复核 raw_diff。",
        })

    if summary.get("link_changes", 0) > 0:
        hints.append({
            "level": "medium",
            "kind": "link_changed",
            "message": "本次改动包含连线变化；连线错误通常会导致 ComfyUI 执行失败。",
        })

    if validation_after.get("error_count", 0) > 0:
        hints.append({
            "level": "high",
            "kind": "validation_errors",
            "message": "改完后存在校验错误，不建议提交给 ComfyUI。",
        })

    if validation_after.get("warning_count", 0) > 0:
        hints.append({
            "level": "low",
            "kind": "validation_warnings",
            "message": "改完后存在校验警告，不一定会失败，但建议检查。",
        })

    if not planned_ops:
        hints.append({
            "level": "medium",
            "kind": "no_ops",
            "message": "没有生成或提供任何 ops，本次 workflow 实际不会变化。",
        })

    return hints


def self_check_workflow_edit(workflow: Json, ops: Optional[List[Dict[str, Any]]] = None, text: str = "", strict: bool = False) -> Dict[str, Any]:
    """Apply ops on a copy, then return before/after validation and diff.

    This function does not call ComfyUI and does not write files.
    """
    before = copy.deepcopy(workflow)
    planned_ops = _normalize_ops(ops, text)

    validation_before = validate_workflow(before, strict=strict)
    after = apply_ops(before, planned_ops) if planned_ops else copy.deepcopy(before)
    validation_after = validate_workflow(after, strict=strict)
    diff = diff_workflows(before, after)
    human = diff_to_human(diff)
    hints = _risk_hints(diff, validation_after, planned_ops)

    return {
        "success": True,
        "text": text,
        "ops": planned_ops,
        "changed": diff.get("summary", {}).get("added_nodes", 0) > 0
            or diff.get("summary", {}).get("removed_nodes", 0) > 0
            or diff.get("summary", {}).get("changed_nodes", 0) > 0,
        "safe_to_submit": bool(validation_after.get("valid")) and not any(h.get("level") == "high" for h in hints),
        "review": {
            "what_changed": human,
            "risk_hints": hints,
            "validation_before": {
                "valid": validation_before.get("valid"),
                "error_count": validation_before.get("error_count"),
                "warning_count": validation_before.get("warning_count"),
            },
            "validation_after": {
                "valid": validation_after.get("valid"),
                "error_count": validation_after.get("error_count"),
                "warning_count": validation_after.get("warning_count"),
            },
        },
        "validation_before": validation_before,
        "validation_after": validation_after,
        "raw_diff": diff,
        "after_inspect": inspect_workflow(after),
        "workflow_after": after,
    }
