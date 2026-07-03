#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capability.registry

Static provider registry for common ComfyUI capabilities.

This file intentionally contains deterministic engineering knowledge only.
It does not call an LLM and does not modify workflows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    category: str
    class_keywords: Sequence[str]
    provider: str = "ComfyUI"
    priority: int = 50
    native: bool = False
    experimental: bool = False
    depends_on: Sequence[str] = field(default_factory=tuple)
    enables: Sequence[str] = field(default_factory=tuple)
    risks: Sequence[str] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)


class CapabilityRegistry:
    """Registry of known capability detectors."""

    def __init__(self, specs: Iterable[CapabilitySpec] | None = None):
        self._specs: Dict[str, CapabilitySpec] = {}
        for spec in specs or default_specs():
            self.register(spec)

    def register(self, spec: CapabilitySpec) -> None:
        if not spec.name:
            raise ValueError("Capability name must not be empty.")
        self._specs[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec | None:
        return self._specs.get(name)

    def list(self) -> List[CapabilitySpec]:
        return list(self._specs.values())

    def names(self) -> List[str]:
        return sorted(self._specs.keys())

    def manifest(self) -> Dict[str, object]:
        return {
            "success": True,
            "registry": "workflow-capability-registry",
            "version": "1.0",
            "capability_count": len(self._specs),
            "capabilities": [
                {
                    "name": s.name,
                    "category": s.category,
                    "provider": s.provider,
                    "priority": s.priority,
                    "native": s.native,
                    "experimental": s.experimental,
                    "depends_on": list(s.depends_on),
                    "enables": list(s.enables),
                    "class_keywords": list(s.class_keywords),
                }
                for s in self.list()
            ],
        }


def default_specs() -> List[CapabilitySpec]:
    """Built-in capability specs.

    Keywords are intentionally broad because ComfyUI custom nodes often use
    inconsistent class names. Detection confidence is calculated later.
    """
    return [
        CapabilitySpec(
            name="txt2img",
            category="generation",
            class_keywords=("emptylatent", "textencode", "ksampler", "sampler", "saveimage"),
            provider="ComfyUI-Core",
            priority=100,
            native=True,
            depends_on=("sampling", "prompting", "latent_source"),
            notes=("Text-to-image is inferred when prompt, latent and sampling are present.",),
        ),
        CapabilitySpec(
            name="img2img",
            category="generation",
            class_keywords=("loadimage", "vaeencode", "imageonlycheckpoint", "latentfrombatch"),
            provider="ComfyUI-Core",
            priority=95,
            native=True,
            depends_on=("image_input", "sampling"),
        ),
        CapabilitySpec(
            name="inpainting",
            category="generation",
            class_keywords=("inpaint", "mask", "setlatentnoise", "vaeinpaint"),
            provider="ComfyUI-Core/Extensions",
            priority=80,
            depends_on=("image_input", "mask_input", "sampling"),
        ),
        CapabilitySpec(
            name="sampling",
            category="core",
            class_keywords=("ksampler", "sampler", "samplercustom", "sampleradvanced"),
            provider="ComfyUI-Core",
            priority=100,
            native=True,
        ),
        CapabilitySpec(
            name="prompting",
            category="core",
            class_keywords=("cliptextencode", "textencode", "prompt"),
            provider="ComfyUI-Core",
            priority=100,
            native=True,
        ),
        CapabilitySpec(
            name="latent_source",
            category="core",
            class_keywords=("emptylatent", "latentimage", "sd3emptylatent", "fluxlatent"),
            provider="ComfyUI-Core",
            priority=100,
            native=True,
        ),
        CapabilitySpec(
            name="checkpoint_model",
            category="model",
            class_keywords=("checkpointloader", "ckptloader", "unetloader", "diffusionmodelload", "modelsampling"),
            provider="ComfyUI-Core",
            priority=100,
            native=True,
        ),
        CapabilitySpec(
            name="vae",
            category="model",
            class_keywords=("vaeloader", "vaedecode", "vaeencode"),
            provider="ComfyUI-Core",
            priority=90,
            native=True,
        ),
        CapabilitySpec(
            name="lora",
            category="model_modifier",
            class_keywords=("lora", "loraloader", "powerlora", "easy lora"),
            provider="ComfyUI-Core/Extensions",
            priority=80,
            depends_on=("checkpoint_model",),
            risks=("Excessive LoRA strength may overfit style or damage identity consistency.",),
        ),
        CapabilitySpec(
            name="controlnet",
            category="conditioning",
            class_keywords=("controlnet", "applycontrolnet", "controlnetloader"),
            provider="ComfyUI-Core/Extensions",
            priority=80,
            depends_on=("image_input", "conditioning"),
            risks=("ControlNet may over-constrain composition if strength is too high.",),
        ),
        CapabilitySpec(
            name="ipadapter",
            category="conditioning",
            class_keywords=("ipadapter", "ip-adapter", "clipvision", "imageprompt"),
            provider="IPAdapter/Extensions",
            priority=70,
            depends_on=("image_input",),
        ),
        CapabilitySpec(
            name="upscale",
            category="postprocess",
            class_keywords=("upscale", "esrgan", "ultrasharp", "imagescale", "latentupscale"),
            provider="ComfyUI-Core/Extensions",
            priority=70,
            depends_on=("image_output",),
        ),
        CapabilitySpec(
            name="hires_fix",
            category="postprocess",
            class_keywords=("hires", "latentupscale", "upscale latent", "secondpass", "ultimate"),
            provider="ComfyUI-Core/Extensions",
            priority=65,
            depends_on=("sampling", "upscale"),
        ),
        CapabilitySpec(
            name="face_detailer",
            category="postprocess",
            class_keywords=("facedetailer", "face detailer", "bboxdetector", "segm detector", "impact"),
            provider="Impact Pack/Extensions",
            priority=60,
            depends_on=("image_output",),
            experimental=True,
        ),
        CapabilitySpec(
            name="regional_prompt",
            category="conditioning",
            class_keywords=("regional", "area conditioning", "conditioningarea", "attention couple", "couple"),
            provider="Extensions",
            priority=55,
            depends_on=("prompting",),
            experimental=True,
        ),
        CapabilitySpec(
            name="video",
            category="generation",
            class_keywords=("video", "animatediff", "svd", "wan", "ltx", "frame", "vhs"),
            provider="Video Extensions",
            priority=50,
            experimental=True,
        ),
        CapabilitySpec(
            name="image_input",
            category="io",
            class_keywords=("loadimage", "imageinput", "previewimage"),
            provider="ComfyUI-Core",
            priority=80,
            native=True,
        ),
        CapabilitySpec(
            name="mask_input",
            category="io",
            class_keywords=("loadmask", "mask", "imagemask", "masktoimage"),
            provider="ComfyUI-Core/Extensions",
            priority=75,
        ),
        CapabilitySpec(
            name="image_output",
            category="io",
            class_keywords=("saveimage", "previewimage", "imageoutput"),
            provider="ComfyUI-Core",
            priority=100,
            native=True,
        ),
        CapabilitySpec(
            name="flux_pipeline",
            category="pipeline",
            class_keywords=("flux", "dualcliploader", "cliptextencodeflux", "unetloader", "modelsamplingflux"),
            provider="ComfyUI-Core/Flux",
            priority=90,
            depends_on=("sampling", "prompting"),
        ),
        CapabilitySpec(
            name="sdxl_pipeline",
            category="pipeline",
            class_keywords=("sdxl", "cliptextencodesdxl", "checkpointloadersimple"),
            provider="ComfyUI-Core/SDXL",
            priority=85,
            depends_on=("sampling", "prompting"),
        ),
    ]


_DEFAULT_REGISTRY = CapabilityRegistry()


def default_registry() -> CapabilityRegistry:
    return _DEFAULT_REGISTRY
