#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic capability detector for workflow JSON."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

from .models import Capability, CapabilityProvider, WorkflowIdentity
from .registry import CapabilityRegistry, default_registry
from .utils import class_type_of, iter_workflow_nodes, lower_join, node_inputs, stable_json_hash, utc_now_iso


def infer_pipeline(workflow: Dict[str, Any], class_blob: str) -> str:
    text = class_blob.lower()
    if "flux" in text or "dualcliploader" in text:
        return "Flux"
    if "sdxl" in text or "cliptextencodesdxl" in text:
        return "SDXL"
    if "sd3" in text:
        return "SD3"
    if "wan" in text or "animatediff" in text or "svd" in text:
        return "Video"
    if "checkpointloadersimple" in text or "ksampler" in text:
        return "StableDiffusion"
    return "Unknown"


def detect_provider(class_type: str) -> str:
    c = class_type.lower()
    if "impact" in c or "detailer" in c or "bbox" in c:
        return "Impact Pack/Extension"
    if "ipadapter" in c or "clipvision" in c:
        return "IPAdapter/Extension"
    if "controlnet" in c:
        return "ComfyUI-ControlNet"
    if "lora" in c:
        return "ComfyUI-LoRA"
    if "flux" in c:
        return "ComfyUI-Flux"
    if "ultimate" in c or "upscale" in c or "esrgan" in c:
        return "Upscale/Extension"
    return "ComfyUI-Core"


def build_identity(workflow: Dict[str, Any], workflow_name: str = "") -> WorkflowIdentity:
    nodes = list(iter_workflow_nodes(workflow))
    class_blob = lower_join(class_type_of(n) for _, n in nodes)
    link_count = 0
    for _, node in nodes:
        for value in node_inputs(node).values():
            if isinstance(value, list) and len(value) >= 2:
                link_count += 1
    return WorkflowIdentity(
        backend="ComfyUI",
        pipeline=infer_pipeline(workflow, class_blob),
        workflow_hash=stable_json_hash(workflow),
        workflow_name=workflow_name,
        node_count=len(nodes),
        link_count=link_count,
        generated_at=utc_now_iso(),
    )


def _score_keyword_match(keywords: Tuple[str, ...] | List[str], class_type: str, node_text: str) -> float:
    if not keywords:
        return 0.0
    hits = 0
    for kw in keywords:
        k = str(kw).lower().strip()
        if not k:
            continue
        if k in class_type.lower() or k in node_text:
            hits += 1
    return min(1.0, hits / max(1, min(3, len(keywords))))


def detect_capabilities(workflow: Dict[str, Any], registry: CapabilityRegistry | None = None) -> Tuple[WorkflowIdentity, List[Capability], Dict[str, int], List[str]]:
    registry = registry or default_registry()
    nodes = list(iter_workflow_nodes(workflow))
    class_blob = lower_join(class_type_of(n) for _, n in nodes)
    identity = build_identity(workflow, workflow_name="")
    warnings: List[str] = []
    providers_counter: Counter[str] = Counter()
    matches: Dict[str, List[CapabilityProvider]] = defaultdict(list)
    scores: Dict[str, float] = defaultdict(float)

    for node_id, node in nodes:
        class_type = class_type_of(node)
        inputs = node_inputs(node)
        node_text = lower_join([class_type, jsonish(inputs), str(node.get("_meta") or "")])
        provider_name = detect_provider(class_type)
        providers_counter[provider_name] += 1

        for spec in registry.list():
            score = _score_keyword_match(list(spec.class_keywords), class_type, node_text)
            if score <= 0:
                continue
            matches[spec.name].append(
                CapabilityProvider(
                    node_id=str(node_id),
                    class_type=class_type,
                    provider=provider_name or spec.provider,
                    role=spec.category,
                    evidence=[f"matched class/input keyword for capability '{spec.name}'"],
                )
            )
            scores[spec.name] = max(scores[spec.name], score)

    # Composite inference: txt2img requires prompting + latent + sampling + output.
    composite_names = set(matches.keys())
    if {"prompting", "latent_source", "sampling", "image_output"}.issubset(composite_names):
        spec = registry.get("txt2img")
        if spec and "txt2img" not in matches:
            matches["txt2img"] = []
            scores["txt2img"] = 0.92
    if {"image_input", "sampling"}.issubset(composite_names):
        spec = registry.get("img2img")
        if spec and "img2img" not in matches:
            matches["img2img"] = []
            scores["img2img"] = 0.85

    caps: List[Capability] = []
    for name in sorted(matches.keys() | {"txt2img" if scores.get("txt2img") else ""} - {""}):
        spec = registry.get(name)
        if not spec:
            continue
        providers = matches.get(name, [])
        missing_deps = [dep for dep in spec.depends_on if dep not in matches and dep not in scores]
        status = "ready" if not missing_deps else "missing_dependency"
        confidence = max(scores.get(name, 0.0), 0.78 if providers else 0.0)
        if missing_deps:
            confidence = min(confidence, 0.55)
        if spec.experimental and status == "ready":
            status = "experimental"
        caps.append(
            Capability(
                name=name,
                category=spec.category,
                status=status,
                confidence=round(float(confidence), 3),
                priority=spec.priority,
                native=spec.native,
                experimental=spec.experimental,
                providers=providers,
                depends_on=list(spec.depends_on),
                enables=list(spec.enables),
                risks=list(spec.risks),
                notes=list(spec.notes),
            )
        )

    if not caps:
        warnings.append("No known capabilities detected. The workflow may be a UI workflow, custom-node-heavy, or unsupported shape.")

    return identity, sorted(caps, key=lambda c: (-c.priority, c.name)), dict(providers_counter), warnings


def jsonish(value: Any) -> str:
    try:
        import json
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)
