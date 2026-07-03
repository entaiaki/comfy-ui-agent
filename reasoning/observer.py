#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Workflow observer for reasoning.

Observer turns semantic context into neutral facts. It does not infer causes;
that belongs to hypothesis/scoring.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .models import Observation, Severity

Json = Dict[str, Any]


def _get_node(semantics: Mapping[str, Any], node_id: Optional[str]) -> Mapping[str, Any]:
    if not node_id:
        return {}
    nodes = semantics.get("nodes")
    if isinstance(nodes, Mapping):
        node = nodes.get(str(node_id))
        if isinstance(node, Mapping):
            return node
    return {}


def _scalar(node: Mapping[str, Any], key: str, default: Any = None) -> Any:
    scalar = node.get("scalar_inputs")
    if isinstance(scalar, Mapping) and key in scalar:
        return scalar.get(key)
    return default


def observe_workflow(semantics: Mapping[str, Any]) -> List[Observation]:
    observations: List[Observation] = []

    pipeline = str(semantics.get("inferred_pipeline") or "unknown")
    observations.append(Observation("pipeline", pipeline, source="semantics", note="Inferred model/workflow family."))

    counts = semantics.get("counts") if isinstance(semantics.get("counts"), Mapping) else {}
    for key in ("samplers", "checkpoints", "loras", "controlnets", "upscalers", "unknown_nodes"):
        observations.append(Observation(f"count.{key}", counts.get(key, 0), source="semantics"))

    main = semantics.get("main") if isinstance(semantics.get("main"), Mapping) else {}
    sampler_id = main.get("sampler")
    sampler_node = _get_node(semantics, str(sampler_id) if sampler_id else None)
    if sampler_id:
        observations.append(Observation("main.sampler.node_id", str(sampler_id), source="semantics"))
    if sampler_node:
        for field in ("steps", "cfg", "seed", "sampler_name", "scheduler", "denoise"):
            value = _scalar(sampler_node, field, None)
            if value is not None:
                observations.append(Observation(f"sampler.{field}", value, source="workflow"))

    latent_id = main.get("latent")
    latent_node = _get_node(semantics, str(latent_id) if latent_id else None)
    if latent_id:
        observations.append(Observation("main.latent.node_id", str(latent_id), source="semantics"))
    if latent_node:
        for field in ("width", "height", "batch_size"):
            value = _scalar(latent_node, field, None)
            if value is not None:
                observations.append(Observation(f"latent_source.{field}", value, source="workflow"))

    if int(counts.get("unknown_nodes") or 0) > 0:
        observations.append(Observation(
            "warning.unknown_nodes",
            counts.get("unknown_nodes"),
            source="semantics",
            severity=Severity.MEDIUM,
            note="Custom/unknown nodes make aggressive automatic edits riskier.",
        ))
    if int(counts.get("samplers") or 0) > 1:
        observations.append(Observation(
            "warning.multiple_samplers",
            counts.get("samplers"),
            source="semantics",
            severity=Severity.MEDIUM,
            note="Multiple samplers detected; main sampler may not be the intended edit target.",
        ))

    return observations


def observations_to_map(observations: List[Observation]) -> Json:
    return {obs.key: obs.value for obs in observations}
