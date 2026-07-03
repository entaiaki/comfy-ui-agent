#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_ops.py

ComfyUI workflow graph editor utilities.

Goal: let an AI manipulate a ComfyUI API workflow via a small, auditable
"ops" language instead of emitting a full workflow JSON.

ComfyUI API workflow shape (typical):
{
  "prompt": {
     "1": {"class_type": "KSampler", "inputs": {"steps": 20, "model": ["2", 0], ...}},
     ...
  }
}

We treat node ids as strings.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from workflow_resolver import resolve_node_id
from workflow_semantic_resolver import resolve_semantic_target
from workflow_validator import validate_workflow


Json = Dict[str, Any]


class WorkflowOpsError(ValueError):
    pass


def _ensure_prompt_root(workflow: Json) -> Dict[str, Any]:
    if not isinstance(workflow, dict):
        raise WorkflowOpsError("workflow must be a dict")
    if "prompt" in workflow:
        if not isinstance(workflow["prompt"], dict):
            raise WorkflowOpsError("workflow['prompt'] must be a dict")
        return workflow["prompt"]
    # allow raw prompt dict
    if all(isinstance(k, str) for k in workflow.keys()) and all(isinstance(v, dict) for v in workflow.values()):
        return workflow  # type: ignore
    raise WorkflowOpsError("workflow missing 'prompt' root")


def _next_node_id(prompt: Dict[str, Any]) -> str:
    max_id = 0
    for k in prompt.keys():
        try:
            max_id = max(max_id, int(k))
        except Exception:
            continue
    return str(max_id + 1)


def _as_link(value: Any) -> Optional[Tuple[str, int]]:
    # ComfyUI link is usually ["node_id", output_index]
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
        return (value[0], value[1])
    return None


@dataclass
class WorkflowEditor:
    workflow: Json

    def __post_init__(self):
        # work on a deep copy to be safe
        self.workflow = copy.deepcopy(self.workflow)
        self.prompt = _ensure_prompt_root(self.workflow)

    def resolve_node(self, target: Any) -> str:
        return resolve_node_id(self.workflow, target)

    def get_node(self, node_id: str) -> Dict[str, Any]:
        if node_id not in self.prompt:
            raise WorkflowOpsError(f"node not found: {node_id}")
        node = self.prompt[node_id]
        if not isinstance(node, dict):
            raise WorkflowOpsError(f"node {node_id} is not an object")
        return node

    def add_node(self, class_type: str, inputs: Optional[Dict[str, Any]] = None, node_id: Optional[str] = None) -> str:
        if not class_type or not isinstance(class_type, str):
            raise WorkflowOpsError("class_type must be a non-empty string")
        if node_id is None:
            node_id = _next_node_id(self.prompt)
        node_id = str(node_id)
        if node_id in self.prompt:
            raise WorkflowOpsError(f"node already exists: {node_id}")
        self.prompt[node_id] = {
            "class_type": class_type,
            "inputs": inputs or {},
        }
        return node_id

    def remove_node(self, node_id: str, prune_links: bool = True) -> None:
        node_id = str(node_id)
        if node_id not in self.prompt:
            return
        del self.prompt[node_id]
        if prune_links:
            # remove any inputs that point to this node
            for nid, node in self.prompt.items():
                inputs = node.get("inputs", {})
                if not isinstance(inputs, dict):
                    continue
                to_del = []
                for k, v in inputs.items():
                    lk = _as_link(v)
                    if lk and lk[0] == node_id:
                        to_del.append(k)
                for k in to_del:
                    del inputs[k]

    def set_input(self, node_id: str, key: str, value: Any) -> None:
        node = self.get_node(str(node_id))
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            raise WorkflowOpsError(f"node {node_id} inputs is not a dict")
        inputs[str(key)] = value

    def delete_input(self, node_id: str, key: str) -> None:
        node = self.get_node(str(node_id))
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and str(key) in inputs:
            del inputs[str(key)]

    def connect(self, src_node: str, src_output: int, dst_node: str, dst_input: str) -> None:
        # We do minimal validation only (node existence and output index type)
        self.get_node(str(src_node))
        self.get_node(str(dst_node))
        if not isinstance(src_output, int) or src_output < 0:
            raise WorkflowOpsError("src_output must be a non-negative int")
        self.set_input(str(dst_node), str(dst_input), [str(src_node), int(src_output)])

    def export(self) -> Json:
        # ensure wrapped shape
        if "prompt" in self.workflow:
            return self.workflow
        return {"prompt": self.prompt}


def apply_ops(workflow: Json, ops: List[Dict[str, Any]]) -> Json:
    """Apply ops to workflow and return a new workflow JSON."""
    ed = WorkflowEditor(workflow)

    for op in ops:
        if not isinstance(op, dict):
            raise WorkflowOpsError("each op must be an object")
        t = op.get("op")
        if t == "set":
            if "target" in op:
                resolved = resolve_semantic_target(ed.workflow, op["target"])
                if not resolved.input:
                    raise WorkflowOpsError(f"semantic target has no input field: {op['target']!r}")
                node_id = resolved.node_id
                input_key = resolved.input
            else:
                node_id = ed.resolve_node(op["node"])
                input_key = op["input"]
            ed.set_input(node_id, input_key, op.get("value"))
        elif t == "delete_input":
            if "target" in op:
                resolved = resolve_semantic_target(ed.workflow, op["target"])
                if not resolved.input:
                    raise WorkflowOpsError(f"semantic target has no input field: {op['target']!r}")
                node_id = resolved.node_id
                input_key = resolved.input
            else:
                node_id = ed.resolve_node(op["node"])
                input_key = op["input"]
            ed.delete_input(node_id, input_key)
        elif t == "add_node":
            new_id = ed.add_node(op["class_type"], inputs=op.get("inputs"), node_id=op.get("node_id"))
            op["_new_node_id"] = new_id
        elif t == "remove_node":
            node_id = ed.resolve_node(op["node"])
            ed.remove_node(node_id, prune_links=bool(op.get("prune_links", True)))
        elif t == "connect":
            src_node = ed.resolve_node(op["src_node"])
            dst_node = ed.resolve_node(op["dst_node"])
            ed.connect(src_node, int(op.get("src_output", 0)), dst_node, op["dst_input"])
        else:
            raise WorkflowOpsError(f"unknown op: {t}")

    result = ed.export()
    if any(bool(op.get("validate")) for op in ops if isinstance(op, dict)):
        report = validate_workflow(result, strict=False)
        if not report.get("valid"):
            raise WorkflowOpsError("workflow validation failed: " + json.dumps(report, ensure_ascii=False))
    return result


def to_pretty_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
