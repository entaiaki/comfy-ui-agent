#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_semantics.py

Deterministic semantic layer for ComfyUI API workflows.

English -> 中文对应：
- Semantic Layer -> 语义层：把 ComfyUI JSON 节点翻译成人/Agent 能理解的概念。
- WorkflowContext -> 工作流上下文：一个工作流的结构化世界模型。
- SemanticNode -> 语义节点：带有 role/capabilities/confidence 的节点。
- Capability -> 能力：节点能做什么，例如 sampling、prompt_encoding、model_loading。

Design rule:
This module must NOT call any LLM. It is a deterministic compiler-like layer:
ComfyUI Workflow JSON -> WorkflowContext.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from node_registry import describe_class_type, get_node_spec
from workflow_graph import WorkflowGraph, ensure_prompt_root, is_link

Json = Dict[str, Any]


ROLE_CAPABILITIES: Dict[str, List[str]] = {
    "sampler": ["sampling", "edit_seed", "edit_steps", "edit_cfg", "edit_sampler"],
    "checkpoint_loader": ["model_loading", "edit_checkpoint"],
    "vae_loader": ["vae_loading", "edit_vae"],
    "vae_decode": ["latent_to_image"],
    "latent_source": ["latent_canvas", "edit_width", "edit_height", "edit_batch_size"],
    "text_encoder": ["prompt_encoding", "edit_prompt"],
    "clip_loader": ["clip_loading"],
    "model_modifier": ["modify_model"],
    "conditioning_modifier": ["modify_conditioning"],
    "positive_conditioning": ["positive_conditioning"],
    "negative_conditioning": ["negative_conditioning"],
    "positive_prompt": ["prompt_encoding", "edit_positive_prompt"],
    "negative_prompt": ["prompt_encoding", "edit_negative_prompt"],
    "lora_loader": ["model_modifier", "lora", "edit_lora", "edit_lora_strength"],
    "image_loader": ["image_input", "edit_input_image"],
    "image_saver": ["image_output", "save_image", "edit_filename_prefix"],
    "image_preview": ["image_output", "preview_image"],
    "upscaler": ["upscale", "image_resize"],
    "controlnet": ["control_condition", "controlnet"],
    "preprocessor": ["image_preprocess"],
    "unknown": [],
}


ROLE_ALIASES: Dict[str, List[str]] = {
    "sampler": ["sampler", "sampling", "采样器", "采样", "KSampler"],
    "checkpoint_loader": ["checkpoint", "ckpt", "model", "base model", "底模", "模型"],
    "vae_loader": ["vae", "VAE", "vae loader", "VAE加载器"],
    "vae_decode": ["decode", "vae decode", "解码"],
    "latent_source": ["latent", "canvas", "size", "resolution", "画布", "尺寸", "分辨率"],
    "positive_prompt": ["positive", "positive prompt", "正向", "正向提示词", "prompt"],
    "negative_prompt": ["negative", "negative prompt", "反向", "反向提示词"],
    "lora_loader": ["lora", "LoRA", "模型微调", "风格模型"],
    "clip_loader": ["clip", "text encoder model", "文本编码模型"],
    "model_modifier": ["model modifier", "模型调整", "采样模型调整"],
    "conditioning_modifier": ["conditioning modifier", "条件调整", "guidance", "引导"],
    "positive_conditioning": ["positive conditioning", "正向条件"],
    "negative_conditioning": ["negative conditioning", "反向条件"],
    "image_loader": ["input image", "load image", "输入图", "加载图片"],
    "image_saver": ["save", "output", "保存", "输出"],
    "image_preview": ["preview", "预览"],
    "upscaler": ["upscale", "hires", "高清修复", "放大"],
    "controlnet": ["controlnet", "control", "控制网"],
    "preprocessor": ["preprocessor", "预处理"],
}


@dataclass
class SemanticNode:
    id: str
    class_type: Optional[str]
    role: str
    role_zh: str
    capabilities: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    editable_inputs: List[str] = field(default_factory=list)
    link_inputs: List[str] = field(default_factory=list)
    scalar_inputs: Dict[str, Any] = field(default_factory=dict)
    link_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    upstream: List[str] = field(default_factory=list)
    downstream: List[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    meta: Any = None

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass
class WorkflowContext:
    kind: str
    node_count: int
    edge_count: int
    nodes: Dict[str, SemanticNode]
    roles: Dict[str, List[str]]
    samplers: List[str] = field(default_factory=list)
    checkpoints: List[str] = field(default_factory=list)
    vaes: List[str] = field(default_factory=list)
    positive_prompts: List[str] = field(default_factory=list)
    negative_prompts: List[str] = field(default_factory=list)
    latent_sources: List[str] = field(default_factory=list)
    loras: List[str] = field(default_factory=list)
    controlnets: List[str] = field(default_factory=list)
    preprocessors: List[str] = field(default_factory=list)
    upscalers: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    image_inputs: List[str] = field(default_factory=list)
    unknown_nodes: List[str] = field(default_factory=list)
    main_sampler: Optional[str] = None
    main_positive: Optional[str] = None
    main_negative: Optional[str] = None
    main_latent: Optional[str] = None
    main_output: Optional[str] = None
    inferred_pipeline: str = "unknown"
    warnings: List[str] = field(default_factory=list)

    def get_nodes_by_role(self, role: str) -> List[SemanticNode]:
        return [self.nodes[nid] for nid in self.roles.get(role, []) if nid in self.nodes]

    def get_first(self, role: str) -> Optional[SemanticNode]:
        nodes = self.get_nodes_by_role(role)
        return nodes[0] if nodes else None

    def describe(self) -> str:
        parts: List[str] = []
        parts.append(f"Pipeline: {self.inferred_pipeline}")
        parts.append(f"Nodes: {self.node_count}, Edges: {self.edge_count}")
        if self.checkpoints:
            parts.append(f"Models: {len(self.checkpoints)}")
        if self.loras:
            parts.append(f"LoRA: {len(self.loras)}")
        if self.controlnets:
            parts.append(f"ControlNet: {len(self.controlnets)}")
        if self.samplers:
            parts.append(f"Samplers: {len(self.samplers)}")
        if self.positive_prompts:
            parts.append(f"Positive prompts: {len(self.positive_prompts)}")
        if self.negative_prompts:
            parts.append(f"Negative prompts: {len(self.negative_prompts)}")
        if self.latent_sources:
            parts.append(f"Latent sources: {len(self.latent_sources)}")
        if self.outputs:
            parts.append(f"Outputs: {len(self.outputs)}")
        if self.unknown_nodes:
            parts.append(f"Unknown/custom nodes: {len(self.unknown_nodes)}")
        return "; ".join(parts)

    def to_dict(self) -> Json:
        data = asdict(self)
        data["nodes"] = {nid: node.to_dict() for nid, node in self.nodes.items()}
        data["description"] = self.describe()
        return data


def _sorted_node_ids(ids: Iterable[str]) -> List[str]:
    def key(x: str) -> Tuple[int, str]:
        return (0, f"{int(x):020d}") if str(x).isdigit() else (1, str(x))
    return sorted([str(x) for x in ids], key=key)


def _base_role(class_type: Optional[str]) -> Tuple[str, str, float, str, List[str], List[str]]:
    info = describe_class_type(class_type)
    role = str(info.get("role") or "unknown")
    role_zh = str(info.get("role_zh") or "未知节点")
    editable = list(info.get("editable_inputs") or [])
    aliases = list(info.get("aliases") or [])
    confidence = 0.95 if role != "unknown" else 0.0
    reason = "registry" if role != "unknown" else "unknown class_type"

    low = (class_type or "").lower()
    if role == "unknown":
        if "empty" in low and "latent" in low:
            role, role_zh, confidence, reason = "latent_source", "潜空间画布", 0.8, "class_type suggests empty latent canvas"
            editable = ["width", "height", "batch_size"]
        elif "dualclip" in low or ("clip" in low and "loader" in low):
            role, role_zh, confidence, reason = "clip_loader", "CLIP/T5文本编码模型加载器", 0.75, "class_type suggests clip loader"
            editable = ["clip_name", "clip_name1", "clip_name2", "type", "device"]
        elif "modelsampling" in low or ("model" in low and "sampling" in low):
            role, role_zh, confidence, reason = "model_modifier", "模型采样参数调整节点", 0.7, "class_type suggests model sampling modifier"
            editable = ["max_shift", "base_shift", "width", "height"]
        elif "guidance" in low or "conditioning" in low:
            role, role_zh, confidence, reason = "conditioning_modifier", "条件/引导调整节点", 0.7, "class_type suggests conditioning/guidance modifier"
            editable = ["guidance"]
        elif "controlnet" in low:
            role, role_zh, confidence, reason = "controlnet", "ControlNet控制节点", 0.7, "class_type contains controlnet"
        elif "upscale" in low or "upscaler" in low or "resize" in low or "scale" in low:
            role, role_zh, confidence, reason = "upscaler", "放大/缩放节点", 0.65, "class_type suggests upscale/resize"
        elif "preprocessor" in low or "processor" in low or "canny" in low or "depth" in low or "openpose" in low:
            role, role_zh, confidence, reason = "preprocessor", "预处理节点", 0.55, "class_type suggests preprocessor"
        elif "save" in low and "image" in low:
            role, role_zh, confidence, reason = "image_saver", "图片保存器", 0.75, "class_type suggests save image"
        elif "preview" in low and "image" in low:
            role, role_zh, confidence, reason = "image_preview", "图片预览器", 0.75, "class_type suggests preview image"

    capabilities = list(ROLE_CAPABILITIES.get(role, []))
    aliases.extend(ROLE_ALIASES.get(role, []))
    return role, role_zh, confidence, reason, editable, aliases


def _extract_inputs(node: Json) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], List[str]]:
    inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
    if not isinstance(inputs, dict):
        return {}, {}, []
    scalar_inputs: Dict[str, Any] = {}
    link_map: Dict[str, Dict[str, Any]] = {}
    link_inputs: List[str] = []
    for name, value in inputs.items():
        if is_link(value):
            link_inputs.append(str(name))
            link_map[str(name)] = {"node": str(value[0]), "output": int(value[1])}
        else:
            scalar_inputs[str(name)] = value
    return scalar_inputs, link_map, sorted(link_inputs)


def _sampler_prompt_roles(graph: WorkflowGraph, prompt: Json) -> Dict[str, str]:
    """Refine prompt/conditioning nodes connected to sampler.

    ComfyUI workflows often place helper nodes between CLIPTextEncode and KSampler,
    for example FluxGuidance: CLIPTextEncode -> FluxGuidance -> KSampler. We mark
    both the direct conditioning node and the upstream text encoder deterministically.
    """
    result: Dict[str, str] = {}

    def mark_upstream_text_nodes(start_node: str, prompt_role: str, max_depth: int = 4) -> None:
        frontier: List[Tuple[str, int]] = [(start_node, 0)]
        seen: Set[str] = set()
        while frontier:
            current, depth = frontier.pop(0)
            if current in seen or depth > max_depth:
                continue
            seen.add(current)
            raw = prompt.get(current, {})
            class_type = raw.get("class_type") if isinstance(raw, dict) else None
            base_role, *_ = _base_role(class_type)
            if base_role == "text_encoder":
                result[current] = prompt_role
            for edge in graph.upstream_edges(current):
                frontier.append((edge.src_node, depth + 1))

    for sampler_id, node in prompt.items():
        class_type = node.get("class_type") if isinstance(node, dict) else None
        base_role, *_ = _base_role(class_type)
        if base_role != "sampler":
            continue
        inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
        if not isinstance(inputs, dict):
            continue
        for input_name, prompt_role, conditioning_role in (
            ("positive", "positive_prompt", "positive_conditioning"),
            ("negative", "negative_prompt", "negative_conditioning"),
        ):
            value = inputs.get(input_name)
            if not is_link(value):
                continue
            direct_id = str(value[0])
            raw_direct = prompt.get(direct_id, {})
            direct_class = raw_direct.get("class_type") if isinstance(raw_direct, dict) else None
            direct_base_role, *_ = _base_role(direct_class)
            if direct_base_role == "text_encoder":
                result[direct_id] = prompt_role
            else:
                result[direct_id] = conditioning_role
                mark_upstream_text_nodes(direct_id, prompt_role)
    return result


def _infer_pipeline(prompt: Json, nodes: Dict[str, SemanticNode]) -> str:
    class_blob = " ".join(str(n.class_type or "") for n in nodes.values()).lower()
    input_blob = " ".join(
        str(v).lower()
        for n in nodes.values()
        for v in n.scalar_inputs.values()
        if isinstance(v, (str, int, float, bool))
    )
    blob = class_blob + " " + input_blob
    if "flux" in blob or "dualclip" in blob or "clip_l" in blob or "t5" in blob:
        return "flux"
    if "sdxl" in blob:
        return "sdxl"
    if "stable cascade" in blob or "cascade" in blob:
        return "stable_cascade"
    if any(n.role == "sampler" for n in nodes.values()) and any(n.role in {"checkpoint_loader", "lora_loader"} for n in nodes.values()):
        return "stable_diffusion_like"
    return "unknown"


def build_workflow_context(workflow: Json) -> WorkflowContext:
    prompt = ensure_prompt_root(workflow)
    graph = WorkflowGraph(workflow)
    prompt_role_overrides = _sampler_prompt_roles(graph, prompt)

    nodes: Dict[str, SemanticNode] = {}
    roles: Dict[str, List[str]] = {}
    warnings: List[str] = []

    for node_id in _sorted_node_ids(prompt.keys()):
        raw_node = prompt.get(node_id)
        if not isinstance(raw_node, dict):
            continue
        class_type = raw_node.get("class_type")
        role, role_zh, confidence, reason, editable_inputs, aliases = _base_role(class_type)

        if node_id in prompt_role_overrides:
            role = prompt_role_overrides[node_id]
            if role == "positive_prompt":
                role_zh = "正向提示词编码器"
            elif role == "negative_prompt":
                role_zh = "反向提示词编码器"
            elif role == "positive_conditioning":
                role_zh = "正向条件节点"
            elif role == "negative_conditioning":
                role_zh = "反向条件节点"
            confidence = max(confidence, 0.9 if "conditioning" in role else 0.98)
            reason = "connected to sampler conditioning path"

        capabilities = list(dict.fromkeys(ROLE_CAPABILITIES.get(role, []) + ROLE_CAPABILITIES.get(_base_role(class_type)[0], [])))
        aliases = list(dict.fromkeys(aliases + ROLE_ALIASES.get(role, [])))
        scalar_inputs, link_map, actual_link_inputs = _extract_inputs(raw_node)
        registry_link_inputs = list(describe_class_type(class_type).get("link_inputs", []) or [])
        link_inputs = sorted(set(actual_link_inputs + registry_link_inputs))

        upstream = graph.upstream_nodes(node_id, depth=1)
        downstream = graph.downstream_nodes(node_id, depth=1)

        sem = SemanticNode(
            id=node_id,
            class_type=class_type,
            role=role,
            role_zh=role_zh,
            capabilities=capabilities,
            aliases=aliases,
            editable_inputs=editable_inputs,
            link_inputs=link_inputs,
            scalar_inputs=scalar_inputs,
            link_map=link_map,
            upstream=upstream,
            downstream=downstream,
            confidence=confidence,
            reason=reason,
            meta=raw_node.get("_meta"),
        )
        nodes[node_id] = sem
        roles.setdefault(role, []).append(node_id)

    for role in list(roles.keys()):
        roles[role] = _sorted_node_ids(roles[role])

    samplers = roles.get("sampler", [])
    checkpoints = roles.get("checkpoint_loader", [])
    vaes = _sorted_node_ids(roles.get("vae_loader", []) + roles.get("vae_decode", []))
    positives = roles.get("positive_prompt", [])
    negatives = roles.get("negative_prompt", [])
    latents = roles.get("latent_source", [])
    loras = roles.get("lora_loader", [])
    controlnets = roles.get("controlnet", [])
    preprocessors = roles.get("preprocessor", [])
    upscalers = roles.get("upscaler", [])
    outputs = _sorted_node_ids(roles.get("image_saver", []) + roles.get("image_preview", []))
    image_inputs = roles.get("image_loader", [])
    unknowns = roles.get("unknown", [])

    main_sampler = samplers[0] if samplers else None
    main_positive = None
    main_negative = None
    main_latent = None
    if main_sampler and main_sampler in nodes:
        links = nodes[main_sampler].link_map
        if "positive" in links:
            direct_positive = links["positive"].get("node")
            main_positive = positives[0] if positives else direct_positive
        if "negative" in links:
            direct_negative = links["negative"].get("node")
            main_negative = negatives[0] if negatives else direct_negative
        if "latent_image" in links:
            main_latent = links["latent_image"].get("node")

    main_output = outputs[0] if outputs else None

    if not samplers:
        warnings.append("No sampler node was detected. This may be a non-generation utility workflow or an unsupported custom sampler.")
    if not outputs:
        warnings.append("No image output node was detected.")
    if unknowns:
        warnings.append(f"Detected {len(unknowns)} unknown/custom node(s). Add registry specs later if they are important.")

    context = WorkflowContext(
        kind="api",
        node_count=len(nodes),
        edge_count=len(graph.edges),
        nodes=nodes,
        roles=roles,
        samplers=samplers,
        checkpoints=checkpoints,
        vaes=vaes,
        positive_prompts=positives,
        negative_prompts=negatives,
        latent_sources=latents,
        loras=loras,
        controlnets=controlnets,
        preprocessors=preprocessors,
        upscalers=upscalers,
        outputs=outputs,
        image_inputs=image_inputs,
        unknown_nodes=unknowns,
        main_sampler=main_sampler,
        main_positive=main_positive,
        main_negative=main_negative,
        main_latent=main_latent,
        main_output=main_output,
        inferred_pipeline=_infer_pipeline(prompt, nodes),
        warnings=warnings,
    )
    return context


def inspect_semantics(workflow: Json, include_nodes: bool = True) -> Json:
    ctx = build_workflow_context(workflow)
    data = ctx.to_dict()
    if not include_nodes:
        data.pop("nodes", None)
    return data


def summarize_semantics(workflow: Json) -> Json:
    ctx = build_workflow_context(workflow)
    return {
        "kind": ctx.kind,
        "description": ctx.describe(),
        "inferred_pipeline": ctx.inferred_pipeline,
        "node_count": ctx.node_count,
        "edge_count": ctx.edge_count,
        "main": {
            "sampler": ctx.main_sampler,
            "positive_prompt": ctx.main_positive,
            "negative_prompt": ctx.main_negative,
            "latent": ctx.main_latent,
            "output": ctx.main_output,
        },
        "counts": {
            "samplers": len(ctx.samplers),
            "checkpoints": len(ctx.checkpoints),
            "vaes": len(ctx.vaes),
            "positive_prompts": len(ctx.positive_prompts),
            "negative_prompts": len(ctx.negative_prompts),
            "latent_sources": len(ctx.latent_sources),
            "loras": len(ctx.loras),
            "controlnets": len(ctx.controlnets),
            "preprocessors": len(ctx.preprocessors),
            "upscalers": len(ctx.upscalers),
            "outputs": len(ctx.outputs),
            "image_inputs": len(ctx.image_inputs),
            "unknown_nodes": len(ctx.unknown_nodes),
        },
        "roles": ctx.roles,
        "warnings": ctx.warnings,
    }
