#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_rule_planner.py

Deterministic rule-based planner for common ComfyUI workflow edits.

English -> 中文对应：
- Rule-based Planner -> 规则型规划器：不用大模型，按固定规则把意图转成 ops。
- Intent -> 意图：用户想做的事，例如 set_size / set_prompt / set_sampler。
- Semantic Ops -> 语义操作：使用 sampler.steps 这类稳定目标路径的 workflow_ops。

Scope of P5:
This module only plans small, high-confidence edits. It does NOT add LoRA,
ControlNet, nodes, or convert Civitai UI workflows. Those belong to later phases.

Public API:
    plan_workflow_edit(request: dict) -> dict
    plan_ops_from_text(text: str) -> list[dict]

Returned ops are semantic-target ops, for example:
    {"op": "set", "target": "sampler.steps", "value": 30}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Json = Dict[str, Any]


class PlannerError(ValueError):
    """Raised when a request cannot be planned safely."""


@dataclass(frozen=True)
class PlannedEdit:
    """A deterministic plan produced from a small user intent."""

    ops: List[Json]
    summary: str
    intent: str
    confidence: float = 1.0
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Json:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        return data


# Conservative numeric bounds. These are not ComfyUI hard limits; they are
# guardrails to catch accidental bad plans such as 999999x999999.
MIN_SIZE = 64
MAX_SIZE = 4096
SIZE_MULTIPLE_HINT = 8
MIN_STEPS = 1
MAX_STEPS = 300
MIN_CFG = 0.0
MAX_CFG = 50.0
MIN_DENOISE = 0.0
MAX_DENOISE = 1.0


INTENT_ALIASES: Dict[str, str] = {
    "set_steps": "set_sampler_steps",
    "sampler_steps": "set_sampler_steps",
    "steps": "set_sampler_steps",
    "set_sampler_steps": "set_sampler_steps",
    "set_cfg": "set_cfg",
    "cfg": "set_cfg",
    "guidance": "set_cfg",
    "set_seed": "set_seed",
    "seed": "set_seed",
    "set_size": "set_size",
    "size": "set_size",
    "set_resolution": "set_size",
    "resolution": "set_size",
    "set_prompt": "set_positive_prompt",
    "prompt": "set_positive_prompt",
    "set_positive": "set_positive_prompt",
    "positive": "set_positive_prompt",
    "positive_prompt": "set_positive_prompt",
    "set_positive_prompt": "set_positive_prompt",
    "set_negative": "set_negative_prompt",
    "negative": "set_negative_prompt",
    "negative_prompt": "set_negative_prompt",
    "set_negative_prompt": "set_negative_prompt",
    "set_sampler": "set_sampler_name",
    "sampler": "set_sampler_name",
    "sampler_name": "set_sampler_name",
    "set_sampler_name": "set_sampler_name",
    "set_scheduler": "set_scheduler",
    "scheduler": "set_scheduler",
    "set_denoise": "set_denoise",
    "denoise": "set_denoise",
    "set_checkpoint": "set_checkpoint",
    "checkpoint": "set_checkpoint",
    "ckpt": "set_checkpoint",
    "model": "set_checkpoint",
    "set_vae": "set_vae",
    "vae": "set_vae",
    "set_filename_prefix": "set_filename_prefix",
    "filename_prefix": "set_filename_prefix",
    "prefix": "set_filename_prefix",
    "set_batch_size": "set_batch_size",
    "batch": "set_batch_size",
    "batch_size": "set_batch_size",
}


def _normalize_intent(intent: Any) -> str:
    key = str(intent or "").strip().lower().replace("-", "_").replace(" ", "_")
    return INTENT_ALIASES.get(key, key)


def _first_present(data: Json, keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise PlannerError(f"{field} must be an integer")
    try:
        # Allow values such as "30" but reject "30.5" for integer fields.
        text = str(value).strip()
        if re.fullmatch(r"[-+]?\d+", text):
            return int(text)
    except Exception:
        pass
    raise PlannerError(f"{field} must be an integer")


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise PlannerError(f"{field} must be a number")
    try:
        return float(str(value).strip())
    except Exception as exc:
        raise PlannerError(f"{field} must be a number") from exc


def _as_text(value: Any, field: str, allow_empty: bool = False) -> str:
    if value is None:
        raise PlannerError(f"{field} is required")
    text = str(value)
    if not allow_empty and not text.strip():
        raise PlannerError(f"{field} cannot be empty")
    return text


def _validate_steps(steps: int) -> int:
    if steps < MIN_STEPS or steps > MAX_STEPS:
        raise PlannerError(f"steps out of safe range: {steps} (expected {MIN_STEPS}-{MAX_STEPS})")
    return steps


def _validate_cfg(cfg: float) -> float:
    if cfg < MIN_CFG or cfg > MAX_CFG:
        raise PlannerError(f"cfg out of safe range: {cfg} (expected {MIN_CFG}-{MAX_CFG})")
    return cfg


def _validate_denoise(denoise: float) -> float:
    if denoise < MIN_DENOISE or denoise > MAX_DENOISE:
        raise PlannerError(f"denoise out of safe range: {denoise} (expected {MIN_DENOISE}-{MAX_DENOISE})")
    return denoise


def _validate_size_value(value: int, field: str) -> int:
    if value < MIN_SIZE or value > MAX_SIZE:
        raise PlannerError(f"{field} out of safe range: {value} (expected {MIN_SIZE}-{MAX_SIZE})")
    return value


def _size_warnings(width: int, height: int) -> Tuple[str, ...]:
    warnings: List[str] = []
    if width % SIZE_MULTIPLE_HINT != 0 or height % SIZE_MULTIPLE_HINT != 0:
        warnings.append(f"width/height are usually safer as multiples of {SIZE_MULTIPLE_HINT}")
    return tuple(warnings)


def _set(target: str, value: Any) -> Json:
    return {"op": "set", "target": target, "value": value}


def _plan_structured(request: Json) -> Optional[PlannedEdit]:
    """Plan from explicit intent fields. Returns None when no intent is present."""

    intent = _normalize_intent(_first_present(request, ["intent", "action", "task"]))
    if not intent:
        return None

    if intent == "set_sampler_steps":
        steps = _validate_steps(_as_int(_first_present(request, ["steps", "value"]), "steps"))
        return PlannedEdit([_set("sampler.steps", steps)], f"Set sampler steps to {steps}.", intent)

    if intent == "set_cfg":
        cfg = _validate_cfg(_as_float(_first_present(request, ["cfg", "guidance", "value"]), "cfg"))
        return PlannedEdit([_set("sampler.cfg", cfg)], f"Set sampler CFG to {cfg}.", intent)

    if intent == "set_seed":
        seed = _as_int(_first_present(request, ["seed", "value"]), "seed")
        return PlannedEdit([_set("sampler.seed", seed)], f"Set sampler seed to {seed}.", intent)

    if intent == "set_size":
        width = _validate_size_value(_as_int(_first_present(request, ["width", "w"]), "width"), "width")
        height = _validate_size_value(_as_int(_first_present(request, ["height", "h"]), "height"), "height")
        ops = [_set("latent_source.width", width), _set("latent_source.height", height)]
        return PlannedEdit(ops, f"Set latent size to {width}x{height}.", intent, warnings=_size_warnings(width, height))

    if intent == "set_batch_size":
        batch_size = _as_int(_first_present(request, ["batch_size", "batch", "value"]), "batch_size")
        if batch_size < 1 or batch_size > 64:
            raise PlannerError("batch_size out of safe range: expected 1-64")
        return PlannedEdit([_set("latent_source.batch_size", batch_size)], f"Set batch size to {batch_size}.", intent)

    if intent == "set_positive_prompt":
        text = _as_text(_first_present(request, ["text", "prompt", "positive", "value"]), "text", allow_empty=True)
        return PlannedEdit([_set("positive_prompt.text", text)], "Set positive prompt text.", intent)

    if intent == "set_negative_prompt":
        text = _as_text(_first_present(request, ["text", "prompt", "negative", "value"]), "text", allow_empty=True)
        return PlannedEdit([_set("negative_prompt.text", text)], "Set negative prompt text.", intent)

    if intent == "set_sampler_name":
        name = _as_text(_first_present(request, ["sampler_name", "sampler", "name", "value"]), "sampler_name")
        return PlannedEdit([_set("sampler.sampler_name", name.strip())], f"Set sampler name to {name.strip()}.", intent)

    if intent == "set_scheduler":
        scheduler = _as_text(_first_present(request, ["scheduler", "name", "value"]), "scheduler")
        return PlannedEdit([_set("sampler.scheduler", scheduler.strip())], f"Set scheduler to {scheduler.strip()}.", intent)

    if intent == "set_denoise":
        denoise = _validate_denoise(_as_float(_first_present(request, ["denoise", "value"]), "denoise"))
        return PlannedEdit([_set("sampler.denoise", denoise)], f"Set sampler denoise to {denoise}.", intent)

    if intent == "set_checkpoint":
        name = _as_text(_first_present(request, ["ckpt_name", "checkpoint", "model", "name", "value"]), "ckpt_name")
        return PlannedEdit([_set("checkpoint_loader.ckpt_name", name.strip())], f"Set checkpoint to {name.strip()}.", intent)

    if intent == "set_vae":
        name = _as_text(_first_present(request, ["vae_name", "vae", "name", "value"]), "vae_name")
        return PlannedEdit([_set("vae_loader.vae_name", name.strip())], f"Set VAE to {name.strip()}.", intent)

    if intent == "set_filename_prefix":
        prefix = _as_text(_first_present(request, ["filename_prefix", "prefix", "name", "value"]), "filename_prefix")
        return PlannedEdit([_set("image_saver.filename_prefix", prefix.strip())], f"Set filename prefix to {prefix.strip()}.", intent)

    raise PlannerError(f"unsupported intent: {intent}")


def _num_after(patterns: Iterable[str], text: str, cast: Any) -> Optional[Any]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        try:
            return cast(match.group(1))
        except Exception:
            return None
    return None


def _text_after(patterns: Iterable[str], text: str) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if not match:
            continue
        value = match.group(1).strip()
        # Strip simple wrapping quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ['"', "'", "“", "”"]:
            value = value[1:-1].strip()
        return value
    return None


def _find_size(text: str) -> Tuple[Optional[int], Optional[int]]:
    # Common compact form: 1024x768 / 1024×768 / 1024*768
    m = re.search(r"(?<!\d)(\d{2,5})\s*[x×*]\s*(\d{2,5})(?!\d)", text, flags=re.I)
    if m:
        return int(m.group(1)), int(m.group(2))

    width = _num_after([r"width\s*[:=]?\s*(\d+)", r"宽度\D{0,8}(\d+)", r"宽\D{0,8}(\d+)"], text, int)
    height = _num_after([r"height\s*[:=]?\s*(\d+)", r"高度\D{0,8}(\d+)", r"高\D{0,8}(\d+)"], text, int)
    return width, height


def plan_ops_from_text(text: str) -> List[Json]:
    """Extract high-confidence semantic ops from a short instruction string."""

    raw = str(text or "").strip()
    if not raw:
        return []

    ops: List[Json] = []

    steps = _num_after([
        r"steps?\s*[:=]?\s*(\d+)",
        r"采样步数\D{0,8}(\d+)",
        r"步数\D{0,8}(\d+)",
        r"迭代\D{0,8}(\d+)",
    ], raw, int)
    if steps is not None:
        ops.append(_set("sampler.steps", _validate_steps(steps)))

    cfg = _num_after([
        r"cfg\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"guidance\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"提示词引导\D{0,8}([0-9]+(?:\.[0-9]+)?)",
    ], raw, float)
    if cfg is not None:
        ops.append(_set("sampler.cfg", _validate_cfg(cfg)))

    seed = _num_after([
        r"seed\s*[:=]?\s*(-?\d+)",
        r"随机种子\D{0,8}(-?\d+)",
        r"种子\D{0,8}(-?\d+)",
    ], raw, int)
    if seed is not None:
        ops.append(_set("sampler.seed", seed))

    denoise = _num_after([
        r"denoise\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"重绘幅度\D{0,8}([0-9]+(?:\.[0-9]+)?)",
    ], raw, float)
    if denoise is not None:
        ops.append(_set("sampler.denoise", _validate_denoise(denoise)))

    width, height = _find_size(raw)
    if width is not None:
        ops.append(_set("latent_source.width", _validate_size_value(width, "width")))
    if height is not None:
        ops.append(_set("latent_source.height", _validate_size_value(height, "height")))

    batch_size = _num_after([r"batch(?:_size)?\s*[:=]?\s*(\d+)", r"批量\D{0,8}(\d+)"], raw, int)
    if batch_size is not None:
        if batch_size < 1 or batch_size > 64:
            raise PlannerError("batch_size out of safe range: expected 1-64")
        ops.append(_set("latent_source.batch_size", batch_size))

    sampler_name = _text_after([r"sampler(?:_name)?\s*[:=]\s*([^\n,;]+)", r"采样器\s*(?:改成|设置为|=|:)\s*([^\n,;]+)"], raw)
    if sampler_name:
        ops.append(_set("sampler.sampler_name", sampler_name))

    scheduler = _text_after([r"scheduler\s*[:=]\s*([^\n,;]+)", r"调度器\s*(?:改成|设置为|=|:)\s*([^\n,;]+)"], raw)
    if scheduler:
        ops.append(_set("sampler.scheduler", scheduler))

    prefix = _text_after([r"(?:filename_prefix|prefix)\s*[:=]\s*([^\n,;]+)", r"文件名前缀\s*(?:改成|设置为|=|:)\s*([^\n,;]+)"], raw)
    if prefix:
        ops.append(_set("image_saver.filename_prefix", prefix))

    # Prompt extraction is intentionally conservative. Free-form instructions
    # often contain prompt-like text, so we only parse explicit prefixes.
    positive = _text_after([
        r"positive(?:_prompt)?\s*[:=]\s*(.+)$",
        r"prompt\s*[:=]\s*(.+)$",
        r"正向提示词\s*(?:改成|设置为|=|:)\s*(.+)$",
    ], raw)
    if positive is not None:
        ops.append(_set("positive_prompt.text", positive))

    negative = _text_after([
        r"negative(?:_prompt)?\s*[:=]\s*(.+)$",
        r"反向提示词\s*(?:改成|设置为|=|:)\s*(.+)$",
        r"负向提示词\s*(?:改成|设置为|=|:)\s*(.+)$",
    ], raw)
    if negative is not None:
        ops.append(_set("negative_prompt.text", negative))

    return ops


def plan_workflow_edit(request: Json) -> Json:
    """Plan a small workflow edit from structured intent or instruction text.

    Accepted request shapes:
        {"intent": "set_size", "width": 1024, "height": 1024}
        {"intent": "set_sampler_steps", "steps": 30}
        {"text": "steps 30 cfg 7 size 1024x1024"}

    Returns a dict with ops, summary, intent, confidence, warnings.
    """

    if not isinstance(request, dict):
        raise PlannerError("planner request must be a JSON object")

    structured = _plan_structured(request)
    if structured is not None:
        return structured.to_dict()

    text = str(_first_present(request, ["text", "instruction", "natural_language", "query"], "") or "")
    ops = plan_ops_from_text(text)
    if not ops:
        raise PlannerError("could not plan any safe operation from request")

    summary = f"Planned {len(ops)} semantic operation(s) from text."
    return PlannedEdit(ops=ops, summary=summary, intent="text_rules", confidence=0.75).to_dict()


if __name__ == "__main__":  # pragma: no cover - tiny manual smoke test
    examples = [
        {"intent": "set_size", "width": 1024, "height": 1024},
        {"intent": "set_sampler_steps", "steps": 30},
        {"text": "steps 28 cfg 7 size 1024x768 seed 123"},
    ]
    import json

    for item in examples:
        print(json.dumps(plan_workflow_edit(item), ensure_ascii=False, indent=2))
