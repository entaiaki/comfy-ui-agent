#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_validator.py

Basic validation for ComfyUI API workflows.

English -> 中文对应：
- Validator -> 校验器：在发给 ComfyUI 前先检查明显错误。
- Dangling link -> 悬空连线：输入引用了不存在的节点。
- Required link -> 必要连线：比如 KSampler 的 model/positive/negative/latent_image。
"""

from __future__ import annotations

from typing import Any, Dict, List

from node_registry import get_node_spec
from workflow_graph import ensure_prompt_root, is_link

Json = Dict[str, Any]


_REQUIRED_LINKS_BY_ROLE = {
    "sampler": ["model", "positive", "negative", "latent_image"],
    "vae_decode": ["samples", "vae"],
    "image_saver": ["images"],
}


def validate_workflow(workflow: Json, strict: bool = False) -> Dict[str, Any]:
    prompt = ensure_prompt_root(workflow)
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            errors.append({"node": str(node_id), "error": "node is not an object"})
            continue
        class_type = node.get("class_type")
        if not class_type:
            errors.append({"node": str(node_id), "error": "missing class_type"})
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            errors.append({"node": str(node_id), "error": "inputs is not an object"})
            continue

        for input_name, value in inputs.items():
            if is_link(value):
                src = str(value[0])
                out_idx = int(value[1])
                if src not in prompt:
                    errors.append({
                        "node": str(node_id),
                        "input": input_name,
                        "error": "dangling link",
                        "link": value,
                    })
                if out_idx < 0:
                    errors.append({
                        "node": str(node_id),
                        "input": input_name,
                        "error": "negative output index",
                        "link": value,
                    })

        spec = get_node_spec(class_type)
        if spec:
            required = _REQUIRED_LINKS_BY_ROLE.get(spec.role, [])
            for key in required:
                if key not in inputs:
                    msg = {"node": str(node_id), "class_type": class_type, "input": key, "warning": "likely missing required link"}
                    if strict:
                        errors.append({**msg, "error": msg.pop("warning")})
                    else:
                        warnings.append(msg)
                elif not is_link(inputs[key]):
                    warnings.append({"node": str(node_id), "class_type": class_type, "input": key, "warning": "expected a link input"})

    return {
        "valid": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
