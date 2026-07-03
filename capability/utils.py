#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small utilities for capability detection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_json_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def iter_workflow_nodes(workflow: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Yield (node_id, node) pairs from common ComfyUI API workflow shapes."""
    if not isinstance(workflow, dict):
        return

    # Plain API workflow: {"1": {"class_type": ...}, ...}
    for key, value in workflow.items():
        if isinstance(value, dict) and "class_type" in value:
            yield str(key), value

    # Wrapped API workflow: {"prompt": {"1": ...}}
    prompt = workflow.get("prompt")
    if isinstance(prompt, dict):
        for key, value in prompt.items():
            if isinstance(value, dict) and "class_type" in value:
                yield str(key), value

    # UI workflow nodes: {"nodes": [{"id":..., "type":...}, ...]}
    nodes = workflow.get("nodes")
    if isinstance(nodes, list):
        for item in nodes:
            if isinstance(item, dict):
                node_id = str(item.get("id") or item.get("node_id") or "")
                if not node_id:
                    continue
                if "class_type" not in item and "type" in item:
                    item = dict(item)
                    item["class_type"] = item.get("type")
                if "class_type" in item:
                    yield node_id, item


def class_type_of(node: Dict[str, Any]) -> str:
    return str(node.get("class_type") or node.get("type") or "")


def node_inputs(node: Dict[str, Any]) -> Dict[str, Any]:
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def lower_join(values: Iterable[str]) -> str:
    return " ".join(str(v).lower() for v in values if v is not None)


def unique_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        text = str(item)
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result
