#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capability query and matching."""

from __future__ import annotations

from typing import Iterable, List

from .models import Capability, CapabilityQueryResult


ALIASES = {
    "lora": "lora",
    "loras": "lora",
    "control net": "controlnet",
    "controlnet": "controlnet",
    "ipadapter": "ipadapter",
    "ip adapter": "ipadapter",
    "高清修复": "hires_fix",
    "高分辨率修复": "hires_fix",
    "放大": "upscale",
    "超分": "upscale",
    "脸部修复": "face_detailer",
    "面部修复": "face_detailer",
    "动漫": "prompting",
    "文生图": "txt2img",
    "图生图": "img2img",
    "重绘": "inpainting",
    "局部重绘": "inpainting",
    "采样": "sampling",
    "提示词": "prompting",
    "底模": "checkpoint_model",
    "模型": "checkpoint_model",
}


def normalize_query(query: str) -> str:
    text = str(query or "").strip().lower().replace("-", "_").replace(" ", "_")
    return ALIASES.get(text, text)


def match_capabilities(capabilities: Iterable[Capability], query: str, manifest_hash: str = "") -> CapabilityQueryResult:
    q_raw = str(query or "").strip()
    q = normalize_query(q_raw)
    caps = list(capabilities)
    if not q:
        return CapabilityQueryResult(success=True, query=q_raw, matches=caps, manifest_hash=manifest_hash, message="Returned all capabilities.")

    matches: List[Capability] = []
    for cap in caps:
        haystack = " ".join([cap.name, cap.category, " ".join(cap.depends_on), " ".join(cap.notes)]).lower()
        if q == cap.name.lower() or q in haystack:
            matches.append(cap)

    return CapabilityQueryResult(
        success=bool(matches),
        query=q_raw,
        matches=matches,
        missing=[] if matches else [q],
        manifest_hash=manifest_hash,
        message="Capability found." if matches else "Capability not detected in current workflow.",
    )
