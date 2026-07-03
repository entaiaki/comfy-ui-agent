#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_graph.py

Graph helpers for ComfyUI API workflows.

English -> 中文对应：
- Graph -> 图结构：节点和连线组成的结构。
- Edge -> 边/连线：一个节点输出接到另一个节点输入。
- Upstream -> 上游：当前节点依赖的前面节点。
- Downstream -> 下游：依赖当前节点输出的后面节点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

Json = Dict[str, Any]


def ensure_prompt_root(workflow: Json) -> Dict[str, Any]:
    if isinstance(workflow, dict) and isinstance(workflow.get("prompt"), dict):
        return workflow["prompt"]
    if isinstance(workflow, dict) and all(isinstance(k, str) for k in workflow.keys()) and all(isinstance(v, dict) for v in workflow.values()):
        return workflow
    raise ValueError("Only ComfyUI API workflow is supported by workflow_graph. UI workflow must be converted first.")


def is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)


@dataclass(frozen=True)
class Edge:
    src_node: str
    src_output: int
    dst_node: str
    dst_input: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src_node": self.src_node,
            "src_output": self.src_output,
            "dst_node": self.dst_node,
            "dst_input": self.dst_input,
        }


class WorkflowGraph:
    def __init__(self, workflow: Json):
        self.workflow = workflow
        self.prompt = ensure_prompt_root(workflow)
        self.edges: List[Edge] = self._build_edges()

    def _build_edges(self) -> List[Edge]:
        edges: List[Edge] = []
        for dst_node, node in self.prompt.items():
            inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
            if not isinstance(inputs, dict):
                continue
            for dst_input, value in inputs.items():
                if is_link(value):
                    edges.append(Edge(str(value[0]), int(value[1]), str(dst_node), str(dst_input)))
        return edges

    def node(self, node_id: str) -> Dict[str, Any]:
        return self.prompt[str(node_id)]

    def class_type(self, node_id: str) -> Optional[str]:
        node = self.prompt.get(str(node_id), {})
        return node.get("class_type") if isinstance(node, dict) else None

    def upstream_edges(self, node_id: str) -> List[Edge]:
        node_id = str(node_id)
        return [e for e in self.edges if e.dst_node == node_id]

    def downstream_edges(self, node_id: str) -> List[Edge]:
        node_id = str(node_id)
        return [e for e in self.edges if e.src_node == node_id]

    def upstream_nodes(self, node_id: str, depth: int = 1) -> List[str]:
        return self._walk(str(node_id), direction="up", depth=depth)

    def downstream_nodes(self, node_id: str, depth: int = 1) -> List[str]:
        return self._walk(str(node_id), direction="down", depth=depth)

    def _walk(self, start: str, direction: str, depth: int) -> List[str]:
        seen: Set[str] = set()
        frontier: List[Tuple[str, int]] = [(start, 0)]
        out: List[str] = []
        while frontier:
            node_id, d = frontier.pop(0)
            if d >= depth:
                continue
            edges = self.upstream_edges(node_id) if direction == "up" else self.downstream_edges(node_id)
            next_ids = [e.src_node for e in edges] if direction == "up" else [e.dst_node for e in edges]
            for nid in next_ids:
                if nid not in seen and nid != start:
                    seen.add(nid)
                    out.append(nid)
                    frontier.append((nid, d + 1))
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_count": len(self.prompt),
            "edge_count": len(self.edges),
            "edges": [e.to_dict() for e in self.edges],
        }
