#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_resolver.py

Find nodes without relying on hard-coded node ids.

English -> 中文对应：
- Resolver -> 解析器/定位器：把“采样器”“LoRA”等自然语言目标定位到具体节点。
- Query -> 查询条件：class_type、role、input key、title 等。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from node_registry import get_node_spec
from workflow_graph import WorkflowGraph, ensure_prompt_root

Json = Dict[str, Any]


def _text_blob(node_id: str, node: Dict[str, Any]) -> str:
    inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
    meta = node.get("_meta", {}) if isinstance(node, dict) else {}
    parts = [str(node_id), str(node.get("class_type", ""))]
    if isinstance(meta, dict):
        parts.extend(str(v) for v in meta.values())
    if isinstance(inputs, dict):
        parts.extend(str(k) for k in inputs.keys())
        for k, v in inputs.items():
            if isinstance(v, (str, int, float, bool)):
                parts.append(str(v))
    return " ".join(parts).lower()


class NodeResolver:
    def __init__(self, workflow: Json):
        self.workflow = workflow
        self.prompt = ensure_prompt_root(workflow)
        self.graph = WorkflowGraph(workflow)

    def find(self, query: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
        """Return ranked node candidates.

        Supported query keys:
        - id: exact node id
        - class_type: exact or fuzzy class_type
        - role: semantic role from node_registry, e.g. sampler / lora_loader
        - input_key: node must contain this input key
        - text: fuzzy search over id, class_type, meta title, input keys/values
        - connected_to: prefer nodes connected upstream/downstream to this id
        """
        if not isinstance(query, dict):
            raise ValueError("resolver query must be a dict")

        exact_id = query.get("id") or query.get("node")
        if exact_id is not None:
            nid = str(exact_id)
            if nid in self.prompt:
                return [self._candidate(nid, self.prompt[nid], score=100.0, reasons=["exact id"])]
            return []

        class_type_q = str(query.get("class_type", "")).lower().strip()
        role_q = str(query.get("role", "")).lower().strip()
        input_key_q = str(query.get("input_key", "")).strip()
        text_q = str(query.get("text", "")).lower().strip()
        connected_to = str(query.get("connected_to", "")).strip() if query.get("connected_to") is not None else ""

        connected_near = set()
        if connected_to and connected_to in self.prompt:
            connected_near.update(self.graph.upstream_nodes(connected_to, depth=2))
            connected_near.update(self.graph.downstream_nodes(connected_to, depth=2))

        out: List[Dict[str, Any]] = []
        for node_id, node in self.prompt.items():
            if not isinstance(node, dict):
                continue
            score = 0.0
            reasons: List[str] = []
            ct = str(node.get("class_type", ""))
            ct_low = ct.lower()
            spec = get_node_spec(ct)
            inputs = node.get("inputs", {}) if isinstance(node.get("inputs", {}), dict) else {}

            if class_type_q:
                if ct_low == class_type_q:
                    score += 60
                    reasons.append("exact class_type")
                elif class_type_q in ct_low or ct_low in class_type_q:
                    score += 35
                    reasons.append("fuzzy class_type")

            if role_q and spec:
                if spec.role.lower() == role_q or role_q in spec.aliases:
                    score += 55
                    reasons.append("semantic role")

            if input_key_q and input_key_q in inputs:
                score += 25
                reasons.append("has input_key")

            if text_q:
                blob = _text_blob(str(node_id), node)
                if text_q in blob:
                    score += 20
                    reasons.append("text match")
                if spec and any(text_q in a.lower() for a in spec.aliases):
                    score += 30
                    reasons.append("alias match")

            if connected_near and str(node_id) in connected_near:
                score += 10
                reasons.append("near connected node")

            if not any([class_type_q, role_q, input_key_q, text_q, connected_near]):
                score = 1
                reasons.append("listed")

            if score > 0:
                out.append(self._candidate(str(node_id), node, score, reasons))

        out.sort(key=lambda x: (-x["score"], int(x["id"]) if str(x["id"]).isdigit() else 10**9))
        return out[:limit]

    def one(self, query: Dict[str, Any], required: bool = True) -> Optional[str]:
        candidates = self.find(query, limit=2)
        if not candidates:
            if required:
                raise ValueError(f"No node matched query: {query}")
            return None
        # Avoid dangerous ambiguous edits: if top two scores tie, ask caller to disambiguate.
        if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
            raise ValueError(f"Ambiguous node query {query}; candidates: {candidates[:2]}")
        return str(candidates[0]["id"])

    def _candidate(self, node_id: str, node: Dict[str, Any], score: float, reasons: List[str]) -> Dict[str, Any]:
        ct = node.get("class_type")
        spec = get_node_spec(ct)
        return {
            "id": str(node_id),
            "class_type": ct,
            "role": spec.role if spec else "unknown",
            "role_zh": spec.role_zh if spec else "未知节点",
            "score": score,
            "reasons": reasons,
            "input_keys": sorted(list((node.get("inputs") or {}).keys())) if isinstance(node.get("inputs"), dict) else [],
            "meta": node.get("_meta"),
        }


def resolve_node_id(workflow: Json, target: Any) -> str:
    """Resolve either a direct id string/int or a query dict into a node id."""
    if isinstance(target, (str, int)):
        return str(target)
    if isinstance(target, dict):
        return NodeResolver(workflow).one(target, required=True)  # type: ignore[return-value]
    raise ValueError(f"Unsupported node target: {target!r}")
