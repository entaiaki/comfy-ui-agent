#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_inspect.py

Helpers to inspect either:
- ComfyUI API workflow (prompt dict: {"1": {class_type, inputs...}, ...} or {"prompt": {...}})
- ComfyUI UI workflow (has top-level keys: nodes, links, last_node_id...)

We return a compact summary useful for LLM planning.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional

from node_registry import describe_class_type, list_known_nodes
from workflow_graph import WorkflowGraph
from workflow_validator import validate_workflow
from workflow_semantics import summarize_semantics


Json = Dict[str, Any]


def detect_workflow_kind(data: Json) -> str:
    if isinstance(data, dict) and "nodes" in data and isinstance(data.get("nodes"), list):
        return "ui"
    if isinstance(data, dict) and "prompt" in data and isinstance(data.get("prompt"), dict):
        return "api_wrapped"
    if isinstance(data, dict) and all(isinstance(k, str) for k in data.keys()) and all(isinstance(v, dict) for v in data.values()):
        # heuristic: api prompt dict
        return "api"
    return "unknown"


def inspect_api_workflow(data: Json) -> Json:
    prompt = data.get("prompt") if "prompt" in data else data
    nodes_out: List[Json] = []

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}

        link_inputs = []
        for k, v in inputs.items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int):
                link_inputs.append(k)

        class_info = describe_class_type(class_type)
        nodes_out.append(
            {
                "id": str(node_id),
                "class_type": class_type,
                "role": class_info.get("role"),
                "role_zh": class_info.get("role_zh"),
                "description": class_info.get("description"),
                "editable_inputs": class_info.get("editable_inputs", []),
                "input_keys": sorted(list(inputs.keys())),
                "link_inputs": sorted(link_inputs),
                "meta": node.get("_meta"),
            }
        )

    graph = WorkflowGraph(data)
    validation = validate_workflow(data, strict=False)
    semantics = summarize_semantics(data)
    return {
        "kind": "api",
        "node_count": len(nodes_out),
        "edge_count": graph.to_dict()["edge_count"],
        "validation": validation,
        "semantics": semantics,
        "nodes": sorted(nodes_out, key=lambda x: int(x["id"]) if str(x["id"]).isdigit() else 10**9),
        "edges": graph.to_dict()["edges"],
    }


def inspect_ui_workflow(data: Json) -> Json:
    # UI workflow uses numeric ids and a separate links array; we just list node types + widget values
    nodes = data.get("nodes", [])
    out_nodes: List[Json] = []

    for n in nodes:
        if not isinstance(n, dict):
            continue
        out_nodes.append(
            {
                "id": n.get("id"),
                "type": n.get("type"),
                "title": (n.get("title") or (n.get("properties") or {}).get("title")),
                "widgets_values": n.get("widgets_values"),
                "inputs": n.get("inputs"),
                "outputs": n.get("outputs"),
            }
        )

    return {
        "kind": "ui",
        "node_count": len(out_nodes),
        "has_links": isinstance(data.get("links"), list),
        "nodes": out_nodes,
    }


def inspect_workflow(data: Json) -> Json:
    kind = detect_workflow_kind(data)
    if kind in ("api", "api_wrapped"):
        return inspect_api_workflow(data)
    if kind == "ui":
        return inspect_ui_workflow(data)
    return {"kind": "unknown"}


def inspect_registry() -> Json:
    return {"success": True, "known_nodes": list_known_nodes()}
