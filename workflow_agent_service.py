#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_agent_service.py

P6 Agent service layer for the ComfyUI workflow bridge.

English -> 中文对应：
- Agent Service -> Agent 服务层：把“规划、预演、执行准备”串成统一入口。
- Plan -> 规划：把 intent/text 转成 workflow ops。
- Dry Run -> 预演：只在内存里应用 ops 并校验，不提交给 ComfyUI。
- Apply -> 应用：通过调用方把已确认的 ops 交给现有 generate_image/submit 流程。

Design boundary / 设计边界：
This module is intentionally deterministic and does not call any LLM or ComfyUI.
It only orchestrates existing local modules:
    workflow_rule_planner -> workflow_self_check -> normalized apply payload

The HTTP bridge decides whether to execute synchronously (/generate-image style)
or asynchronously (/submit style). Keeping this module side-effect free makes it
safe to test and avoids circular imports.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from workflow_rule_planner import plan_workflow_edit, PlannerError
from workflow_self_check import self_check_workflow_edit
from workflow_semantics import summarize_semantics
from workflow_semantic_resolver import resolve_many_semantic_targets

Json = Dict[str, Any]


class AgentServiceError(ValueError):
    """Raised when an agent request cannot be handled safely."""


@dataclass(frozen=True)
class AgentPlanResult:
    """Structured result for /agent/plan."""

    request_id: str
    workflow: str
    plan: Json
    ops: List[Json]
    semantics: Json
    created_at: float

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class AgentDryRunResult:
    """Structured result for /agent/dry-run."""

    request_id: str
    workflow: str
    plan: Json
    ops: List[Json]
    dry_run: Json
    semantics: Json
    created_at: float

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class AgentApplyPreparation:
    """Side-effect-free preparation result for /agent/apply.

    The bridge may pass ``generation_request`` to ``generate_image`` after this
    object confirms the dry run is safe.
    """

    request_id: str
    workflow: str
    plan: Json
    ops: List[Json]
    dry_run: Json
    generation_request: Json
    created_at: float

    def to_dict(self) -> Json:
        return asdict(self)


def _now() -> float:
    return time.time()


def _request_id(request: Json) -> str:
    value = request.get("request_id") or request.get("id")
    return str(value) if value else str(uuid.uuid4())


def _first_present(data: Json, keys: Tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _ensure_dict(value: Any, field: str) -> Json:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AgentServiceError(f"{field} must be an object")
    return value


def _ensure_ops(value: Any) -> List[Json]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgentServiceError("ops must be a list")
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise AgentServiceError(f"ops[{i}] must be an object")
    return copy.deepcopy(value)


def _planning_payload(request: Json) -> Json:
    """Build a payload accepted by workflow_rule_planner.

    Supported input styles:
    1. Explicit intent fields at top level:
       {"intent":"set_size", "width":1024, "height":1024}
    2. Nested intent:
       {"intent":{"name":"set_size", "width":1024, "height":1024}}
    3. Text instruction:
       {"text":"steps 30 size 1024x1024"}
    4. Already planned ops:
       {"ops":[...]}
    """
    if request.get("ops") is not None:
        return {"ops": _ensure_ops(request.get("ops")), "summary": "Use caller-provided ops.", "intent": "provided_ops", "confidence": 1.0, "warnings": []}

    nested_intent = request.get("intent")
    if isinstance(nested_intent, dict):
        payload = copy.deepcopy(nested_intent)
        # Allow top-level generation/request fields to remain separate.
        return payload

    # For simple top-level requests, the rule planner can read the fields as-is.
    payload = copy.deepcopy(request)

    # Normalize common natural-language fields to the planner's text field.
    text = _first_present(request, ("text", "instruction", "user_request", "message", "natural_language"))
    if text is not None and "text" not in payload:
        payload["text"] = text

    return payload


def _make_plan(request: Json) -> Json:
    payload = _planning_payload(request)
    if "ops" in payload and set(payload.keys()).issubset({"ops", "summary", "intent", "confidence", "warnings"}):
        return {
            "ops": payload["ops"],
            "summary": payload.get("summary", "Use caller-provided ops."),
            "intent": payload.get("intent", "provided_ops"),
            "confidence": float(payload.get("confidence", 1.0)),
            "warnings": list(payload.get("warnings", [])),
        }
    try:
        return plan_workflow_edit(payload)
    except PlannerError:
        raise
    except Exception as exc:
        raise AgentServiceError(f"failed to plan workflow edit: {exc}") from exc


def _semantic_snapshot(workflow: Json, include: bool = True) -> Json:
    if not include:
        return {}
    try:
        return summarize_semantics(workflow)
    except Exception as exc:  # Semantic summary should not hide the main result.
        return {"error": str(exc)}


def _resolved_targets(workflow: Json, ops: List[Json]) -> List[Json]:
    """Best-effort target resolution for explainability."""
    targets: List[str] = []
    for op in ops:
        target = op.get("target")
        if isinstance(target, str) and target.strip():
            targets.append(target.strip())
    if not targets:
        return []
    try:
        resolved = resolve_many_semantic_targets(workflow, targets)
        if isinstance(resolved, list):
            return resolved
        return [resolved]
    except Exception as exc:
        return [{"success": False, "error": str(exc), "targets": targets}]


def agent_plan(workflow: Json, request: Json, workflow_name: str = "") -> Json:
    """Return deterministic plan and semantic context.

    No workflow mutation, no ComfyUI call.
    """
    if not isinstance(workflow, dict):
        raise AgentServiceError("workflow must be a dict")
    if not isinstance(request, dict):
        raise AgentServiceError("request must be a dict")

    rid = _request_id(request)
    plan = _make_plan(request)
    ops = _ensure_ops(plan.get("ops"))
    plan = copy.deepcopy(plan)
    plan["resolved_targets"] = _resolved_targets(workflow, ops)

    result = AgentPlanResult(
        request_id=rid,
        workflow=str(workflow_name or request.get("workflow") or ""),
        plan=plan,
        ops=ops,
        semantics=_semantic_snapshot(workflow, include=bool(request.get("include_semantics", True))),
        created_at=_now(),
    )
    return {"success": True, **result.to_dict()}


def agent_dry_run(workflow: Json, request: Json, workflow_name: str = "") -> Json:
    """Plan and dry-run in one deterministic step.

    No file write, no ComfyUI call.
    """
    planned = agent_plan(workflow, request, workflow_name=workflow_name)
    strict = bool(request.get("strict", False))
    include_workflow = bool(request.get("include_workflow", False))

    dry = self_check_workflow_edit(workflow, ops=planned["ops"], text="", strict=strict)
    if not include_workflow:
        dry.pop("workflow_after", None)

    result = AgentDryRunResult(
        request_id=planned["request_id"],
        workflow=str(workflow_name or request.get("workflow") or ""),
        plan=planned["plan"],
        ops=planned["ops"],
        dry_run=dry,
        semantics=planned.get("semantics", {}),
        created_at=_now(),
    )
    return {"success": True, **result.to_dict()}


def _merge_generation_request(request: Json, ops: List[Json]) -> Json:
    """Build a generate_image-compatible request.

    ``generation`` is optional. Top-level generation fields are also preserved
    for backward compatibility. The planned ops always win over caller-provided
    generation.ops because the plan is the audited source of truth.
    """
    generation = _ensure_dict(request.get("generation"), "generation")

    # Start with top-level request to preserve fields accepted by normalize_request,
    # then overlay nested generation fields for clarity.
    blocked = {"intent", "action", "task", "text", "instruction", "user_request", "message", "natural_language", "generation"}
    payload = {k: copy.deepcopy(v) for k, v in request.items() if k not in blocked}
    payload.update(copy.deepcopy(generation))
    payload["ops"] = copy.deepcopy(ops)
    return payload


def agent_prepare_apply(workflow: Json, request: Json, workflow_name: str = "") -> Json:
    """Plan, dry-run, and produce a generate_image-compatible payload.

    This function still does not call ComfyUI. The bridge owns the final side
    effect. Refusing unsafe plans here prevents accidental execution.
    """
    dry_result = agent_dry_run(workflow, request, workflow_name=workflow_name)
    dry = dry_result["dry_run"]

    require_safe = bool(request.get("require_safe", True))
    force = bool(request.get("force", False))
    if require_safe and not force and not dry.get("safe_to_submit"):
        raise AgentServiceError("dry-run is not safe_to_submit; pass force=true only after manual review")

    generation_request = _merge_generation_request(request, dry_result["ops"])
    if workflow_name and not generation_request.get("workflow"):
        generation_request["workflow"] = str(workflow_name)

    result = AgentApplyPreparation(
        request_id=dry_result["request_id"],
        workflow=str(workflow_name or request.get("workflow") or ""),
        plan=dry_result["plan"],
        ops=dry_result["ops"],
        dry_run=dry,
        generation_request=generation_request,
        created_at=_now(),
    )
    return {"success": True, **result.to_dict()}
