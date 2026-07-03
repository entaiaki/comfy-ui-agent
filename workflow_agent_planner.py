#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_agent_planner.py

Small deterministic planner for common natural-language workflow edits.

注意：这不是大模型本身，而是“兜底规划器”。真正的 LLM 可以直接输出 ops；
当用户只说简单需求时，这里先把常见需求转成安全 ops。

Supported request examples:
- "set sampler steps to 30"
- "把采样步数改成30"
- "cfg 7"
- "seed 123456"
- "width 1024 height 1024"
- "filename prefix test"
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

Json = Dict[str, Any]


def _num_after(patterns: List[str], text: str, cast=float):
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            try:
                return cast(m.group(1))
            except Exception:
                return None
    return None


def plan_ops_from_text(text: str) -> List[Dict[str, Any]]:
    raw = text or ""
    low = raw.lower()
    ops: List[Dict[str, Any]] = []

    steps = _num_after([r"steps?\s*[:=]?\s*(\d+)", r"采样步数\D{0,6}(\d+)", r"步数\D{0,6}(\d+)"], raw, int)
    if steps is not None:
        ops.append({"op": "set", "node": {"role": "sampler"}, "input": "steps", "value": steps})

    cfg = _num_after([r"cfg\s*[:=]?\s*([0-9.]+)", r"提示词引导\D{0,6}([0-9.]+)"], raw, float)
    if cfg is not None:
        ops.append({"op": "set", "node": {"role": "sampler"}, "input": "cfg", "value": cfg})

    seed = _num_after([r"seed\s*[:=]?\s*(-?\d+)", r"随机种子\D{0,6}(-?\d+)"], raw, int)
    if seed is not None:
        ops.append({"op": "set", "node": {"role": "sampler"}, "input": "seed", "value": seed})

    width = _num_after([r"width\s*[:=]?\s*(\d+)", r"宽度\D{0,6}(\d+)", r"(\d+)\s*[x×]\s*\d+"], raw, int)
    height = _num_after([r"height\s*[:=]?\s*(\d+)", r"高度\D{0,6}(\d+)", r"\d+\s*[x×]\s*(\d+)"], raw, int)
    if width is not None:
        ops.append({"op": "set", "node": {"role": "latent_source"}, "input": "width", "value": width})
    if height is not None:
        ops.append({"op": "set", "node": {"role": "latent_source"}, "input": "height", "value": height})

    sampler_match = re.search(r"sampler(?:_name)?\s*[:=]\s*([A-Za-z0-9_+ .-]+)", raw, flags=re.I)
    if sampler_match:
        ops.append({"op": "set", "node": {"role": "sampler"}, "input": "sampler_name", "value": sampler_match.group(1).strip()})

    scheduler_match = re.search(r"scheduler\s*[:=]\s*([A-Za-z0-9_+ .-]+)", raw, flags=re.I)
    if scheduler_match:
        ops.append({"op": "set", "node": {"role": "sampler"}, "input": "scheduler", "value": scheduler_match.group(1).strip()})

    prefix_match = re.search(r"(?:filename_prefix|prefix)\s*[:=]\s*([A-Za-z0-9_ -]+)", raw, flags=re.I)
    if prefix_match:
        ops.append({"op": "set", "node": {"role": "image_saver"}, "input": "filename_prefix", "value": prefix_match.group(1).strip()})

    return ops
