#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hypothesis generation for common image/workflow goals."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from .models import Hypothesis, ProblemSpec
from .observer import observations_to_map


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def generate_default_hypotheses(problem: ProblemSpec, observations: Mapping[str, Any]) -> List[Hypothesis]:
    goal = problem.normalized_goal
    out: List[Hypothesis] = []

    steps = _num(observations.get("sampler.steps"), 0)
    cfg = _num(observations.get("sampler.cfg"), 0)
    sampler_name = str(observations.get("sampler.sampler_name") or "")
    pipeline = str(observations.get("pipeline") or "unknown")
    has_upscaler = _num(observations.get("count.upscalers"), 0) > 0
    unknown_nodes = _num(observations.get("count.unknown_nodes"), 0)

    if goal == "sharper":
        out.append(Hypothesis(
            id="sharpness.increase_steps",
            title="Increase sampling steps",
            title_zh="提高采样步数",
            goal=goal,
            target="sampler.steps",
            action="increase",
            rationale="Low or moderate step count can cause under-sampled details; increasing steps is a low-risk first attempt.",
            rationale_zh="步数偏低或中等时容易采样不足，先小幅提高步数通常成本低、风险低。",
            base_score=0.82 if steps and steps < 28 else 0.55,
            cost=0.35,
            risk=0.18,
            tags=("quality", "low_risk"),
        ))
        out.append(Hypothesis(
            id="sharpness.sampler_quality",
            title="Prefer a higher quality sampler/scheduler pair",
            title_zh="考虑更高质量的采样器/调度器组合",
            goal=goal,
            target="sampler.sampler_name",
            action="prefer_quality",
            rationale="Sampler choice affects convergence, edge detail, and texture clarity.",
            rationale_zh="采样器会影响收敛、边缘细节和纹理清晰度。",
            base_score=0.58,
            cost=0.55,
            risk=0.35,
            tags=("quality", "changes_output"),
        ))
        if not has_upscaler:
            out.append(Hypothesis(
                id="sharpness.add_refinement_later",
                title="Consider refinement or upscale later",
                title_zh="后续可考虑高清修复或放大精修",
                goal=goal,
                target="upscaler",
                action="consider_add",
                rationale="If base sampling is already reasonable, a refinement/upscale stage may improve perceived detail.",
                rationale_zh="如果基础采样已经合理，后续高清修复/放大精修可能提升观感细节。",
                base_score=0.42,
                cost=0.8,
                risk=0.55,
                tags=("later", "graph_edit"),
            ))
    elif goal == "faster":
        out.append(Hypothesis(
            id="speed.reduce_steps",
            title="Reduce sampling steps",
            title_zh="降低采样步数",
            goal=goal,
            target="sampler.steps",
            action="decrease",
            rationale="Step count is one of the most direct time costs.",
            rationale_zh="采样步数是最直接的耗时来源之一。",
            base_score=0.85 if steps and steps > 25 else 0.6,
            cost=0.2,
            risk=0.25,
            tags=("performance", "low_cost"),
        ))
        out.append(Hypothesis(
            id="speed.reduce_size",
            title="Reduce base latent size for previews",
            title_zh="预览阶段降低基础画布尺寸",
            goal=goal,
            target="latent_source.width,height",
            action="decrease_preview",
            rationale="Base resolution strongly affects VRAM and runtime.",
            rationale_zh="基础分辨率会明显影响显存和生成时间。",
            base_score=0.62,
            cost=0.35,
            risk=0.35,
            tags=("performance", "preview"),
        ))
    elif goal == "more_prompt_adherence":
        out.append(Hypothesis(
            id="adherence.increase_cfg",
            title="Slightly increase guidance/CFG",
            title_zh="小幅提高 CFG / 引导强度",
            goal=goal,
            target="sampler.cfg",
            action="slightly_increase",
            rationale="Higher guidance can improve prompt adherence but should be adjusted gradually.",
            rationale_zh="更高的引导强度可能更听提示词，但应小幅调整。",
            base_score=0.74 if cfg and cfg < 8 else 0.5,
            cost=0.2,
            risk=0.3,
            tags=("prompt", "low_cost"),
        ))
    elif goal == "more_natural":
        out.append(Hypothesis(
            id="natural.reduce_cfg",
            title="Slightly reduce over-strong guidance",
            title_zh="小幅降低过强 CFG / 引导强度",
            goal=goal,
            target="sampler.cfg",
            action="slightly_decrease",
            rationale="Over-strong guidance can make images harsh, plastic, or over-constrained.",
            rationale_zh="引导过强会让画面生硬、塑料感、约束过重。",
            base_score=0.72 if cfg and cfg > 7 else 0.45,
            cost=0.2,
            risk=0.25,
            tags=("natural", "low_cost"),
        ))
    elif goal == "anime_style":
        out.append(Hypothesis(
            id="style.prompt_anime",
            title="Strengthen anime style in positive prompt",
            title_zh="在正向提示词中强化动漫风格描述",
            goal=goal,
            target="positive_prompt.text",
            action="append_style_tokens",
            rationale="Style goals are often safest to start from prompt changes before model/LoRA changes.",
            rationale_zh="风格目标优先从提示词改起，比直接换模型或加 LoRA 风险更低。",
            base_score=0.65,
            cost=0.2,
            risk=0.22,
            tags=("style", "prompt_first"),
        ))
        out.append(Hypothesis(
            id="style.lora_anime_later",
            title="Consider an anime LoRA/model later",
            title_zh="后续再考虑动漫 LoRA / 模型",
            goal=goal,
            target="lora_loader",
            action="consider_add",
            rationale="A style LoRA can be effective but requires model availability and graph edits.",
            rationale_zh="风格 LoRA 可能有效，但依赖本地模型文件并需要改图结构。",
            base_score=0.45,
            cost=0.75,
            risk=0.5,
            tags=("style", "later", "graph_edit"),
        ))
    elif goal == "photorealistic":
        out.append(Hypothesis(
            id="style.prompt_photo",
            title="Strengthen photographic realism in prompt",
            title_zh="在提示词中强化摄影真实感",
            goal=goal,
            target="positive_prompt.text",
            action="append_style_tokens",
            rationale="Prompt-level style adjustment is low risk and preserves the current workflow.",
            rationale_zh="提示词层面的风格调整风险低，且不破坏当前工作流。",
            base_score=0.62,
            cost=0.2,
            risk=0.2,
            tags=("style", "prompt_first"),
        ))
    elif goal == "fix_bad_anatomy":
        out.append(Hypothesis(
            id="anatomy.prompt_negative",
            title="Add anatomy artifacts to negative prompt",
            title_zh="在反向提示词中加入人体/手部错误约束",
            goal=goal,
            target="negative_prompt.text",
            action="append_negative_tokens",
            rationale="Prompt constraints are the lowest-risk first step for mild anatomy artifacts.",
            rationale_zh="轻微人体/手部问题可先从反向提示词约束开始，风险最低。",
            base_score=0.55,
            cost=0.2,
            risk=0.25,
            tags=("quality", "prompt_first"),
        ))
        out.append(Hypothesis(
            id="anatomy.controlnet_later",
            title="Consider pose/control guidance later",
            title_zh="后续考虑姿态/控制约束",
            goal=goal,
            target="controlnet",
            action="consider_add",
            rationale="Severe anatomy issues are often better solved by pose/control guidance, but that is a graph-level change.",
            rationale_zh="严重人体问题往往需要姿态/控制约束，但这是图结构级修改。",
            base_score=0.48,
            cost=0.85,
            risk=0.55,
            tags=("later", "graph_edit"),
        ))
    else:
        out.append(Hypothesis(
            id="unknown.ask_clarification",
            title="Ask for clarification before editing",
            title_zh="修改前先确认目标",
            goal=goal,
            target="",
            action="ask",
            rationale="The goal is not recognized with enough confidence for deterministic workflow edits.",
            rationale_zh="目标识别不够稳定，不适合确定性自动修改工作流。",
            base_score=0.3,
            cost=0.1,
            risk=0.1,
            tags=("clarification",),
        ))

    if pipeline == "flux":
        # Do not block hypotheses, but mark classic CFG assumptions as weaker later via scoring.
        pass
    if unknown_nodes > 0:
        # Same: scorer will add risk/caution.
        pass
    return out
