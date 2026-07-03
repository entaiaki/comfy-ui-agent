#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""node_registry.py

A lightweight knowledge base for common ComfyUI nodes.

English -> 中文对应：
- Registry -> 注册表/知识库：记录节点是什么、常见输入是什么。
- Semantic role -> 语义角色：节点在工作流里的用途，例如采样、提示词编码、模型加载。
- Editable input -> 可编辑输入：AI 可以安全修改的参数。

This file intentionally has no ComfyUI runtime dependency. It is used by
Inspector / Resolver / Validator / Planner to avoid hard-coding node ids.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class NodeSpec:
    class_type: str
    role: str
    role_zh: str
    description: str
    editable_inputs: List[str]
    link_inputs: List[str]
    aliases: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_COMMON_SPECS: Dict[str, NodeSpec] = {
    "KSampler": NodeSpec(
        class_type="KSampler",
        role="sampler",
        role_zh="采样器",
        description="Turns latent noise into an image latent according to model, prompt, seed and sampling parameters.",
        editable_inputs=["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
        link_inputs=["model", "positive", "negative", "latent_image"],
        aliases=["sampler", "采样", "采样器", "ksampler"],
    ),
    "KSamplerAdvanced": NodeSpec(
        class_type="KSamplerAdvanced",
        role="sampler",
        role_zh="高级采样器",
        description="Advanced sampler with start/end step control.",
        editable_inputs=["noise_seed", "steps", "cfg", "sampler_name", "scheduler", "start_at_step", "end_at_step", "add_noise", "return_with_leftover_noise"],
        link_inputs=["model", "positive", "negative", "latent_image"],
        aliases=["advanced sampler", "高级采样", "ksampleradvanced"],
    ),
    "CLIPTextEncode": NodeSpec(
        class_type="CLIPTextEncode",
        role="text_encoder",
        role_zh="文本编码器",
        description="Encodes positive or negative text prompt into conditioning for the sampler.",
        editable_inputs=["text"],
        link_inputs=["clip"],
        aliases=["prompt", "positive prompt", "negative prompt", "提示词", "正向提示词", "反向提示词", "clip text"],
    ),
    "CheckpointLoaderSimple": NodeSpec(
        class_type="CheckpointLoaderSimple",
        role="checkpoint_loader",
        role_zh="基础模型加载器",
        description="Loads checkpoint model, CLIP and VAE.",
        editable_inputs=["ckpt_name"],
        link_inputs=[],
        aliases=["checkpoint", "model loader", "模型", "大模型", "ckpt"],
    ),
    "VAELoader": NodeSpec(
        class_type="VAELoader",
        role="vae_loader",
        role_zh="VAE加载器",
        description="Loads a VAE model.",
        editable_inputs=["vae_name"],
        link_inputs=[],
        aliases=["vae", "vae loader"],
    ),
    "VAEDecode": NodeSpec(
        class_type="VAEDecode",
        role="vae_decode",
        role_zh="VAE解码器",
        description="Decodes sampled latent into image pixels.",
        editable_inputs=[],
        link_inputs=["samples", "vae"],
        aliases=["decode", "解码", "vae decode"],
    ),
    "EmptyLatentImage": NodeSpec(
        class_type="EmptyLatentImage",
        role="latent_source",
        role_zh="空潜空间图像",
        description="Creates an empty latent canvas with width, height and batch size.",
        editable_inputs=["width", "height", "batch_size"],
        link_inputs=[],
        aliases=["size", "尺寸", "分辨率", "latent"],
    ),
    "SaveImage": NodeSpec(
        class_type="SaveImage",
        role="image_saver",
        role_zh="图片保存器",
        description="Saves output image files.",
        editable_inputs=["filename_prefix"],
        link_inputs=["images"],
        aliases=["save", "保存", "output", "输出"],
    ),
    "PreviewImage": NodeSpec(
        class_type="PreviewImage",
        role="image_preview",
        role_zh="图片预览器",
        description="Previews output image files.",
        editable_inputs=[],
        link_inputs=["images"],
        aliases=["preview", "预览"],
    ),
    "LoraLoader": NodeSpec(
        class_type="LoraLoader",
        role="lora_loader",
        role_zh="LoRA加载器",
        description="Applies LoRA weights to model and CLIP.",
        editable_inputs=["lora_name", "strength_model", "strength_clip"],
        link_inputs=["model", "clip"],
        aliases=["lora", "lora loader", "loraloader"],
    ),
    "Power Lora Loader (rgthree)": NodeSpec(
        class_type="Power Lora Loader (rgthree)",
        role="lora_loader",
        role_zh="增强LoRA加载器",
        description="rgthree enhanced LoRA loader.",
        editable_inputs=["lora", "strength", "strength_model", "strength_clip"],
        link_inputs=["model", "clip"],
        aliases=["power lora", "rgthree lora", "增强lora"],
    ),
    "LoadImage": NodeSpec(
        class_type="LoadImage",
        role="image_loader",
        role_zh="图片加载器",
        description="Loads an input image from disk.",
        editable_inputs=["image", "upload"],
        link_inputs=[],
        aliases=["load image", "加载图片", "输入图片"],
    ),
}


def get_node_spec(class_type: Optional[str]) -> Optional[NodeSpec]:
    if not class_type:
        return None
    if class_type in _COMMON_SPECS:
        return _COMMON_SPECS[class_type]
    # Heuristic fallback for custom node names.
    low = class_type.lower()
    if "ksampler" in low or "sampler" in low:
        return _COMMON_SPECS["KSampler"]
    if "cliptextencode" in low or ("clip" in low and "text" in low):
        return _COMMON_SPECS["CLIPTextEncode"]
    if "lora" in low:
        return _COMMON_SPECS["LoraLoader"]
    if "checkpoint" in low or "unet" in low or "model" in low and "loader" in low:
        return _COMMON_SPECS["CheckpointLoaderSimple"]
    if "vae" in low and "decode" in low:
        return _COMMON_SPECS["VAEDecode"]
    if "vae" in low and "loader" in low:
        return _COMMON_SPECS["VAELoader"]
    if "saveimage" in low or "save image" in low:
        return _COMMON_SPECS["SaveImage"]
    return None


def describe_class_type(class_type: Optional[str]) -> Dict[str, Any]:
    spec = get_node_spec(class_type)
    if spec:
        return spec.to_dict()
    return {
        "class_type": class_type,
        "role": "unknown",
        "role_zh": "未知节点",
        "description": "Unknown/custom ComfyUI node. Inspector can still expose its inputs and links.",
        "editable_inputs": [],
        "link_inputs": [],
        "aliases": [],
    }


def list_known_nodes() -> List[Dict[str, Any]]:
    return [spec.to_dict() for spec in _COMMON_SPECS.values()]
