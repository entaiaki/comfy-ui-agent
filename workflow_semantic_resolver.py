#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_semantic_resolver.py

Deterministic semantic target resolver for ComfyUI API workflows.

English -> 中文对应：
- Semantic Resolver -> 语义定位器：把 sampler.steps、positive_prompt.text 这类语义路径定位到具体 node_id + input_key。
- Target Path -> 目标路径：面向 Agent 的稳定路径，不直接暴露节点 ID。
- Node Role -> 节点角色：sampler / positive_prompt / latent_source 等语义身份。

Design rule:
This module must NOT call any LLM. It is a deterministic bridge:
WorkflowContext + semantic target -> concrete workflow edit location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from workflow_semantics import WorkflowContext, SemanticNode, build_workflow_context

Json = Dict[str, Any]


class SemanticResolveError(ValueError):
    """Raised when a semantic target cannot be resolved safely."""


@dataclass(frozen=True)
class ResolvedTarget:
    """Concrete destination resolved from a semantic target."""

    target: str
    node_id: str
    input: Optional[str]
    role: str
    class_type: Optional[str]
    confidence: float
    reason: str
    candidates: List[Dict[str, Any]]

    def to_dict(self) -> Json:
        return asdict(self)


# Canonical role aliases. Keep this list conservative; fuzzy expansion belongs in
# workflow_semantics.py, while this resolver should prefer predictable paths.
ROLE_ALIASES: Dict[str, str] = {
    # sampler
    "sampler": "sampler",
    "sampling": "sampler",
    "ksampler": "sampler",
    "采样器": "sampler",
    "采样": "sampler",
    # prompts
    "prompt": "positive_prompt",
    "positive": "positive_prompt",
    "positive_prompt": "positive_prompt",
    "positiveprompt": "positive_prompt",
    "正向": "positive_prompt",
    "正向提示词": "positive_prompt",
    "negative": "negative_prompt",
    "negative_prompt": "negative_prompt",
    "negativeprompt": "negative_prompt",
    "反向": "negative_prompt",
    "负向": "negative_prompt",
    "反向提示词": "negative_prompt",
    "负向提示词": "negative_prompt",
    # latent / canvas
    "latent": "latent_source",
    "latent_source": "latent_source",
    "canvas": "latent_source",
    "size": "latent_source",
    "resolution": "latent_source",
    "画布": "latent_source",
    "尺寸": "latent_source",
    "分辨率": "latent_source",
    # model / checkpoint
    "model": "checkpoint_loader",
    "checkpoint": "checkpoint_loader",
    "ckpt": "checkpoint_loader",
    "base_model": "checkpoint_loader",
    "basemodel": "checkpoint_loader",
    "底模": "checkpoint_loader",
    "模型": "checkpoint_loader",
    # vae
    "vae": "vae_loader",
    "vae_loader": "vae_loader",
    # lora
    "lora": "lora_loader",
    "loras": "lora_loader",
    "lora_loader": "lora_loader",
    # output
    "output": "image_saver",
    "save": "image_saver",
    "saver": "image_saver",
    "image_saver": "image_saver",
    "保存": "image_saver",
    "输出": "image_saver",
    # image input
    "image": "image_loader",
    "input_image": "image_loader",
    "image_input": "image_loader",
    "load_image": "image_loader",
    "输入图": "image_loader",
    "加载图片": "image_loader",
    # control/upscale
    "controlnet": "controlnet",
    "control": "controlnet",
    "控制网": "controlnet",
    "upscale": "upscaler",
    "upscaler": "upscaler",
    "hires": "upscaler",
    "高清修复": "upscaler",
    "放大": "upscaler",
}

# Common input aliases by role. Values are ordered fallbacks. The resolver picks
# the first input that exists on the selected node.
INPUT_ALIASES_BY_ROLE: Dict[str, Dict[str, List[str]]] = {
    "sampler": {
        "seed": ["seed", "noise_seed"],
        "steps": ["steps"],
        "cfg": ["cfg", "guidance", "scale"],
        "sampler": ["sampler_name", "sampler"],
        "sampler_name": ["sampler_name", "sampler"],
        "scheduler": ["scheduler"],
        "denoise": ["denoise"],
        "latent": ["latent_image", "latent"],
        "model": ["model"],
        "positive": ["positive"],
        "negative": ["negative"],
    },
    "positive_prompt": {
        "text": ["text", "prompt"],
        "prompt": ["text", "prompt"],
        "clip": ["clip"],
    },
    "negative_prompt": {
        "text": ["text", "prompt"],
        "prompt": ["text", "prompt"],
        "clip": ["clip"],
    },
    "latent_source": {
        "width": ["width", "W"],
        "height": ["height", "H"],
        "batch": ["batch_size", "batch"],
        "batch_size": ["batch_size", "batch"],
        "size": ["width", "height"],
        "resolution": ["width", "height"],
    },
    "checkpoint_loader": {
        "model": ["ckpt_name", "model_name", "unet_name"],
        "checkpoint": ["ckpt_name", "model_name", "unet_name"],
        "ckpt": ["ckpt_name", "model_name", "unet_name"],
        "ckpt_name": ["ckpt_name", "model_name", "unet_name"],
        "name": ["ckpt_name", "model_name", "unet_name"],
    },
    "vae_loader": {
        "vae": ["vae_name", "vae"],
        "vae_name": ["vae_name", "vae"],
        "name": ["vae_name", "vae"],
    },
    "lora_loader": {
        "lora": ["lora_name", "lora"],
        "lora_name": ["lora_name", "lora"],
        "name": ["lora_name", "lora"],
        "strength": ["strength_model", "strength_clip", "strength"],
        "strength_model": ["strength_model", "strength"],
        "strength_clip": ["strength_clip", "strength"],
        "model_strength": ["strength_model", "strength"],
        "clip_strength": ["strength_clip", "strength"],
    },
    "image_saver": {
        "prefix": ["filename_prefix", "prefix"],
        "filename_prefix": ["filename_prefix", "prefix"],
        "name": ["filename_prefix", "prefix"],
        "image": ["images", "image"],
    },
    "image_loader": {
        "image": ["image", "image_path", "filename"],
        "filename": ["image", "image_path", "filename"],
        "path": ["image_path", "image", "filename"],
    },
    "controlnet": {
        "strength": ["strength", "control_strength"],
        "start": ["start_percent", "start"],
        "end": ["end_percent", "end"],
        "image": ["image"],
    },
    "upscaler": {
        "scale": ["scale", "upscale_by", "factor"],
        "width": ["width"],
        "height": ["height"],
        "method": ["upscale_method", "method"],
    },
}

# Useful direct shorthands. This intentionally covers only high-confidence,
# common edit destinations.
DIRECT_TARGETS: Dict[str, Tuple[str, str]] = {
    "cfg": ("sampler", "cfg"),
    "steps": ("sampler", "steps"),
    "seed": ("sampler", "seed"),
    "sampler_name": ("sampler", "sampler_name"),
    "scheduler": ("sampler", "scheduler"),
    "denoise": ("sampler", "denoise"),
    "width": ("latent_source", "width"),
    "height": ("latent_source", "height"),
    "batch_size": ("latent_source", "batch_size"),
    "positive": ("positive_prompt", "text"),
    "prompt": ("positive_prompt", "text"),
    "negative": ("negative_prompt", "text"),
    "filename_prefix": ("image_saver", "filename_prefix"),
    "ckpt_name": ("checkpoint_loader", "ckpt_name"),
    "vae_name": ("vae_loader", "vae_name"),
}

_INDEX_RE = re.compile(r"^(?P<name>[^\[.]+)(?:\[(?P<index>\d+)\])?$")


def _normalise_token(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("-", "_").replace(" ", "_").replace(".", "_")
    return text.lower()


def _parse_role_token(token: str) -> Tuple[str, Optional[int]]:
    """Parse role token such as lora[1] or loras.0 into canonical role + index."""
    token = str(token or "").strip()
    if not token:
        raise SemanticResolveError("empty semantic role token")

    # loras.1 is accepted as syntactic sugar before split in resolve_target.
    match = _INDEX_RE.match(token)
    if not match:
        raise SemanticResolveError(f"invalid semantic role token: {token!r}")

    raw_name = match.group("name")
    index_text = match.group("index")
    normalised = _normalise_token(raw_name)
    role = ROLE_ALIASES.get(normalised, normalised)
    index = int(index_text) if index_text is not None else None
    return role, index


def _role_nodes(ctx: WorkflowContext, role: str) -> List[SemanticNode]:
    # Some public semantic roles are backed by multiple internal roles.
    if role == "vae_loader":
        ids = ctx.roles.get("vae_loader", []) or ctx.roles.get("vae_decode", [])
    elif role == "image_saver":
        ids = ctx.roles.get("image_saver", []) or ctx.roles.get("image_preview", [])
    else:
        ids = ctx.roles.get(role, [])
    return [ctx.nodes[nid] for nid in ids if nid in ctx.nodes]


def _main_node_for_role(ctx: WorkflowContext, role: str) -> Optional[SemanticNode]:
    main_map = {
        "sampler": ctx.main_sampler,
        "positive_prompt": ctx.main_positive,
        "negative_prompt": ctx.main_negative,
        "latent_source": ctx.main_latent,
        "image_saver": ctx.main_output,
    }
    main_id = main_map.get(role)
    if main_id and main_id in ctx.nodes:
        return ctx.nodes[main_id]
    nodes = _role_nodes(ctx, role)
    return nodes[0] if nodes else None


def _select_node(ctx: WorkflowContext, role: str, index: Optional[int] = None) -> SemanticNode:
    nodes = _role_nodes(ctx, role)
    if not nodes:
        raise SemanticResolveError(f"No node found for semantic role: {role}")

    if index is None:
        main = _main_node_for_role(ctx, role)
        if main is not None:
            return main
        if len(nodes) == 1:
            return nodes[0]
        raise SemanticResolveError(
            f"Ambiguous role {role!r}: {len(nodes)} candidates. Use {role}[0], {role}[1], ..."
        )

    if index < 0 or index >= len(nodes):
        raise SemanticResolveError(f"Index out of range for role {role!r}: {index}; candidates={len(nodes)}")
    return nodes[index]


def _input_keys(node: SemanticNode) -> List[str]:
    keys = set(node.scalar_inputs.keys()) | set(node.link_map.keys()) | set(node.editable_inputs) | set(node.link_inputs)
    return sorted(str(k) for k in keys)


def _resolve_input(node: SemanticNode, requested_input: Optional[str]) -> Optional[str]:
    if requested_input is None or requested_input == "":
        return None

    requested = _normalise_token(requested_input)
    available = _input_keys(node)
    available_norm = {_normalise_token(k): k for k in available}

    if requested in available_norm:
        return available_norm[requested]

    aliases = INPUT_ALIASES_BY_ROLE.get(node.role, {})
    for candidate in aliases.get(requested, []):
        norm = _normalise_token(candidate)
        if norm in available_norm:
            return available_norm[norm]

    # Last safe fallback: if the alias points to a standard field that is not
    # currently present but is declared editable by registry/semantics, allow it.
    for candidate in aliases.get(requested, []):
        if candidate in node.editable_inputs:
            return candidate

    raise SemanticResolveError(
        f"Input {requested_input!r} was not found on node {node.id} ({node.class_type}). "
        f"Available inputs: {available}"
    )


def _candidate_summary(nodes: Sequence[SemanticNode]) -> List[Dict[str, Any]]:
    return [
        {
            "id": n.id,
            "role": n.role,
            "class_type": n.class_type,
            "inputs": _input_keys(n),
            "confidence": n.confidence,
        }
        for n in nodes
    ]


def parse_semantic_target(target: Any) -> Tuple[str, Optional[str], Optional[int]]:
    """Parse semantic target into (role, input, index).

    Supported examples:
    - "sampler.steps"
    - "lora[1].strength_model"
    - "loras.0.strength_clip"
    - "cfg"  -> sampler.cfg
    - {"role": "sampler", "input": "cfg"}
    - {"target": "positive_prompt.text"}
    """
    if isinstance(target, dict):
        nested = target.get("target")
        if nested:
            role, input_key, index = parse_semantic_target(nested)
        else:
            role_token = target.get("role") or target.get("node") or target.get("semantic")
            if role_token is None:
                raise SemanticResolveError("semantic target dict needs 'target' or 'role'")
            role, index = _parse_role_token(str(role_token))
            input_key = target.get("input") or target.get("field")
        if target.get("index") is not None:
            index = int(target["index"])
        if target.get("input") is not None:
            input_key = str(target["input"])
        return role, str(input_key) if input_key is not None else None, index

    text = str(target or "").strip()
    if not text:
        raise SemanticResolveError("empty semantic target")

    text_norm = _normalise_token(text)
    if text_norm in DIRECT_TARGETS:
        role, input_key = DIRECT_TARGETS[text_norm]
        return role, input_key, None

    # Accept loras.0.strength as loras[0].strength.
    parts = [p for p in re.split(r"[./]", text) if p != ""]
    if len(parts) >= 3 and parts[1].isdigit():
        parts = [f"{parts[0]}[{parts[1]}]"] + parts[2:]
    if not parts:
        raise SemanticResolveError(f"invalid semantic target: {target!r}")

    role, index = _parse_role_token(parts[0])
    input_key = ".".join(parts[1:]) if len(parts) > 1 else None
    return role, input_key, index


def resolve_semantic_target(workflow: Json, target: Any) -> ResolvedTarget:
    """Resolve a semantic target into concrete node_id and input key.

    This is the public API used by workflow_ops and bridge endpoints.
    """
    ctx = build_workflow_context(workflow)
    role, input_key, index = parse_semantic_target(target)
    node = _select_node(ctx, role, index=index)
    resolved_input = _resolve_input(node, input_key)

    candidates = _candidate_summary(_role_nodes(ctx, role))
    reason_parts = [f"role={role}", f"node={node.id}"]
    if index is not None:
        reason_parts.append(f"index={index}")
    if resolved_input:
        reason_parts.append(f"input={resolved_input}")

    return ResolvedTarget(
        target=str(target),
        node_id=node.id,
        input=resolved_input,
        role=node.role,
        class_type=node.class_type,
        confidence=max(0.0, min(1.0, node.confidence)),
        reason="; ".join(reason_parts),
        candidates=candidates,
    )


def resolve_many_semantic_targets(workflow: Json, targets: Iterable[Any]) -> List[Json]:
    return [resolve_semantic_target(workflow, target).to_dict() for target in targets]


def list_semantic_targets(workflow: Json) -> Json:
    """Return stable editable semantic targets for an API workflow."""
    ctx = build_workflow_context(workflow)
    targets: List[Json] = []

    for role, ids in sorted(ctx.roles.items()):
        nodes = [ctx.nodes[nid] for nid in ids if nid in ctx.nodes]
        if not nodes:
            continue
        for idx, node in enumerate(nodes):
            prefix = role if len(nodes) == 1 else f"{role}[{idx}]"
            keys = _input_keys(node)
            if not keys:
                targets.append({
                    "target": prefix,
                    "node_id": node.id,
                    "role": role,
                    "class_type": node.class_type,
                    "input": None,
                    "editable": False,
                })
                continue
            for key in keys:
                editable = key in node.editable_inputs or key in node.scalar_inputs
                targets.append({
                    "target": f"{prefix}.{key}",
                    "node_id": node.id,
                    "role": role,
                    "class_type": node.class_type,
                    "input": key,
                    "editable": bool(editable),
                })

    return {
        "description": ctx.describe(),
        "inferred_pipeline": ctx.inferred_pipeline,
        "main": {
            "sampler": ctx.main_sampler,
            "positive_prompt": ctx.main_positive,
            "negative_prompt": ctx.main_negative,
            "latent": ctx.main_latent,
            "output": ctx.main_output,
        },
        "targets": targets,
        "count": len(targets),
        "warnings": ctx.warnings,
    }
