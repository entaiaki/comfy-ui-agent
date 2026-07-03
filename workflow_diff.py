#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_diff.py

Small, readable diff tools for ComfyUI API workflows.

English -> 中文对应：
- Diff -> 差异：修改前后哪里变了。
- Audit trail -> 审计记录：让本地 agent 和用户能回头检查每一步。
- Minimal change -> 最小改动：只改需要改的字段，不乱碰其它节点。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Set, Tuple

from workflow_graph import ensure_prompt_root, is_link

Json = Dict[str, Any]


def _safe_prompt(workflow: Json) -> Dict[str, Any]:
    return ensure_prompt_root(workflow)


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _input_kind(value: Any) -> str:
    if is_link(value):
        return "link"
    return "value"


def diff_workflows(before: Json, after: Json) -> Dict[str, Any]:
    """Return a compact workflow diff.

    Output is deliberately simple JSON so another local agent can read it.
    """
    b_prompt = _safe_prompt(before)
    a_prompt = _safe_prompt(after)

    b_ids: Set[str] = set(str(k) for k in b_prompt.keys())
    a_ids: Set[str] = set(str(k) for k in a_prompt.keys())

    def _sort_key(nid: str):
        # stable sort for mixed ids like "11:742" / "37:1014:1109" / "2"
        parts = str(nid).split(":")
        key = []
        for p in parts:
            if p.isdigit():
                key.append((0, int(p)))
            else:
                key.append((1, p))
        return tuple(key)

    added_nodes = sorted(a_ids - b_ids, key=_sort_key)
    removed_nodes = sorted(b_ids - a_ids, key=_sort_key)

    changed_nodes: List[Dict[str, Any]] = []
    link_changes: List[Dict[str, Any]] = []
    value_changes: List[Dict[str, Any]] = []

    for node_id in sorted(b_ids & a_ids, key=_sort_key):
        b_node = b_prompt.get(node_id, {})
        a_node = a_prompt.get(node_id, {})
        if not isinstance(b_node, dict) or not isinstance(a_node, dict):
            if _stable(b_node) != _stable(a_node):
                changed_nodes.append({"node": node_id, "change": "node_object_changed"})
            continue

        node_change: Dict[str, Any] = {"node": node_id}
        any_change = False

        if b_node.get("class_type") != a_node.get("class_type"):
            node_change["class_type"] = {"before": b_node.get("class_type"), "after": a_node.get("class_type")}
            any_change = True

        b_inputs = b_node.get("inputs", {}) if isinstance(b_node.get("inputs", {}), dict) else {}
        a_inputs = a_node.get("inputs", {}) if isinstance(a_node.get("inputs", {}), dict) else {}
        b_keys = set(str(k) for k in b_inputs.keys())
        a_keys = set(str(k) for k in a_inputs.keys())

        input_changes: List[Dict[str, Any]] = []
        for key in sorted(b_keys | a_keys):
            exists_before = key in b_inputs
            exists_after = key in a_inputs
            b_val = b_inputs.get(key)
            a_val = a_inputs.get(key)
            if exists_before and exists_after and _stable(b_val) == _stable(a_val):
                continue
            entry = {
                "input": key,
                "before": b_val if exists_before else None,
                "after": a_val if exists_after else None,
                "change": "added" if (not exists_before and exists_after) else "removed" if (exists_before and not exists_after) else "updated",
                "kind_before": _input_kind(b_val) if exists_before else None,
                "kind_after": _input_kind(a_val) if exists_after else None,
            }
            input_changes.append(entry)
            if entry["kind_before"] == "link" or entry["kind_after"] == "link":
                link_changes.append({"node": node_id, **entry})
            else:
                value_changes.append({"node": node_id, **entry})

        if input_changes:
            node_change["inputs"] = input_changes
            any_change = True

        if any_change:
            changed_nodes.append(node_change)

    added_node_details = []
    for node_id in added_nodes:
        node = a_prompt[node_id]
        added_node_details.append({
            "node": node_id,
            "class_type": node.get("class_type") if isinstance(node, dict) else None,
            "input_keys": sorted(list((node.get("inputs") or {}).keys())) if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else [],
        })

    removed_node_details = []
    for node_id in removed_nodes:
        node = b_prompt[node_id]
        removed_node_details.append({
            "node": node_id,
            "class_type": node.get("class_type") if isinstance(node, dict) else None,
        })

    return {
        "summary": {
            "added_nodes": len(added_nodes),
            "removed_nodes": len(removed_nodes),
            "changed_nodes": len(changed_nodes),
            "value_changes": len(value_changes),
            "link_changes": len(link_changes),
        },
        "added_nodes": added_node_details,
        "removed_nodes": removed_node_details,
        "changed_nodes": changed_nodes,
        "value_changes": value_changes,
        "link_changes": link_changes,
    }


def diff_to_human(diff: Dict[str, Any], max_items: int = 12) -> List[str]:
    """Convert diff JSON into short Chinese review lines."""
    lines: List[str] = []
    summary = diff.get("summary", {})
    lines.append(
        f"新增节点 {summary.get('added_nodes', 0)} 个，删除节点 {summary.get('removed_nodes', 0)} 个，"
        f"修改节点 {summary.get('changed_nodes', 0)} 个；其中普通参数 {summary.get('value_changes', 0)} 处，连线 {summary.get('link_changes', 0)} 处。"
    )

    shown = 0
    for node in diff.get("added_nodes", []):
        if shown >= max_items:
            break
        lines.append(f"新增节点 #{node.get('node')}：{node.get('class_type')}。")
        shown += 1

    for node in diff.get("removed_nodes", []):
        if shown >= max_items:
            break
        lines.append(f"删除节点 #{node.get('node')}：{node.get('class_type')}。")
        shown += 1

    for change in diff.get("value_changes", []):
        if shown >= max_items:
            break
        lines.append(
            f"节点 #{change.get('node')} 的 {change.get('input')}："
            f"{change.get('before')} -> {change.get('after')}。"
        )
        shown += 1

    for change in diff.get("link_changes", []):
        if shown >= max_items:
            break
        lines.append(
            f"节点 #{change.get('node')} 的连线输入 {change.get('input')}："
            f"{change.get('before')} -> {change.get('after')}。"
        )
        shown += 1

    total_detail = len(diff.get("added_nodes", [])) + len(diff.get("removed_nodes", [])) + len(diff.get("value_changes", [])) + len(diff.get("link_changes", []))
    if total_detail > shown:
        lines.append(f"还有 {total_detail - shown} 处细节未展开，可看 raw_diff。")
    return lines
