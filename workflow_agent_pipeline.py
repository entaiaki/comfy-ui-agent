#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_agent_pipeline.py

v12 Agent Pipeline Orchestrator for the ComfyUI Agent framework.

English -> 中文对应：
- Pipeline Orchestrator -> 流水线编排器：把 Reasoner、Decision、Planner、Dry-run 串成一次可审查流程。
- Side-effect free -> 无副作用：默认不写 workflow、不调用 ComfyUI、不出图。
- Gate -> 闸门：每个阶段根据状态决定是否继续，避免低置信/高风险建议被自动执行。
- Dry-run -> 预演：用 workflow_self_check 检查 ops 应用后的差异与风险，但不真正提交给 ComfyUI。

Design boundary / 设计边界：
This module coordinates existing components only. It does not invent new image
editing capabilities, does not add LoRA / ControlNet / feedback / memory, and
does not mutate workflow JSON. It is the stable bridge between reasoning and
execution.

v14 addition: optional Experience Memory retrieval can provide advisory evidence
for the pipeline, but it never bypasses Reasoner, Decision, Planner, or dry-run
gates.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Json = Dict[str, Any]


class PipelineStage(str, Enum):
    NORMALIZE = "normalize"
    REASON = "reason"
    DECIDE = "decide"
    PLAN = "plan"
    DRY_RUN = "dry_run"
    COMPLETE = "complete"


class PipelineStatus(str, Enum):
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(frozen=True)
class PipelinePolicy:
    """Controls how far the pipeline may proceed automatically.

    auto_plan: if false, stop after decision.
    auto_dry_run: if false, stop after planning.
    require_accepted_decision: only plan/dry-run when Decision Engine accepts.
    require_plan_success: only dry-run when Planner succeeds.
    include_workflow: whether dry-run response may include patched workflow JSON.
    strict: forwarded to workflow_self_check.
    """

    auto_plan: bool = True
    auto_dry_run: bool = True
    require_accepted_decision: bool = True
    require_plan_success: bool = True
    include_workflow: bool = False
    strict: bool = False

    @classmethod
    def from_request(cls, request: Mapping[str, Any]) -> "PipelinePolicy":
        raw = request.get("pipeline") if isinstance(request.get("pipeline"), Mapping) else request

        def b(name: str, default: bool) -> bool:
            value = raw.get(name, default)
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y", "on"}
            return bool(value)

        return cls(
            auto_plan=b("auto_plan", cls.auto_plan),
            auto_dry_run=b("auto_dry_run", cls.auto_dry_run),
            require_accepted_decision=b("require_accepted_decision", cls.require_accepted_decision),
            require_plan_success=b("require_plan_success", cls.require_plan_success),
            include_workflow=b("include_workflow", cls.include_workflow),
            strict=b("strict", cls.strict),
        )

    def to_dict(self) -> Json:
        return asdict(self)


@dataclass(frozen=True)
class PipelineGate:
    """One checkpoint in the pipeline."""

    stage: PipelineStage
    passed: bool
    message: str
    message_zh: str = ""
    data: Json = field(default_factory=dict)

    def to_dict(self) -> Json:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


@dataclass(frozen=True)
class PipelineResult:
    """Complete /agent/pipeline response."""

    success: bool
    request_id: str
    status: PipelineStatus
    policy: PipelinePolicy
    workflow_name: str = ""
    reasoning: Optional[Json] = None
    decision: Optional[Json] = None
    plan: Optional[Json] = None
    dry_run: Optional[Json] = None
    memory: Optional[Json] = None
    selected_ops: Tuple[Json, ...] = ()
    ready_to_apply: bool = False
    apply_request: Optional[Json] = None
    gates: Tuple[PipelineGate, ...] = ()
    warnings: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    trace: Tuple[Json, ...] = ()
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Json:
        return {
            "success": self.success,
            "request_id": self.request_id,
            "status": self.status.value,
            "policy": self.policy.to_dict(),
            "workflow": self.workflow_name,
            "reasoning": self.reasoning,
            "decision": self.decision,
            "plan": self.plan,
            "dry_run": self.dry_run,
            "memory": self.memory,
            "selected_ops": list(self.selected_ops),
            "ready_to_apply": self.ready_to_apply,
            "apply_request": self.apply_request,
            "gates": [gate.to_dict() for gate in self.gates],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "trace": list(self.trace),
            "created_at": self.created_at,
        }


def _request_id(request: Mapping[str, Any]) -> str:
    value = request.get("request_id") or request.get("id")
    return str(value) if value else str(uuid.uuid4())


def _as_dict(value: Any) -> Json:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _decision_status(decision_bundle: Mapping[str, Any]) -> str:
    decision = _as_dict(decision_bundle.get("decision"))
    return str(decision.get("status") or decision_bundle.get("status") or "").strip().lower()


def _planner_request_from_decision(request: Mapping[str, Any], reasoning: Mapping[str, Any], decision_bundle: Mapping[str, Any]) -> Json:
    """Create a Planner-compatible request without changing existing planner APIs."""

    planner_request: Json = dict(request)
    planner_request["reasoning"] = dict(reasoning)
    planner_request["decision"] = dict(decision_bundle)
    planner_request.setdefault("max_candidates", request.get("max_candidates", 3))

    selected_ops = _as_list(decision_bundle.get("selected_ops"))
    if selected_ops:
        # Give the existing planner a direct path, while still preserving the
        # Reasoner result for candidate generation and traceability.
        planner_request.setdefault("ops", selected_ops)
    return planner_request


def _selected_ops_from_plan_or_decision(plan: Mapping[str, Any], decision_bundle: Mapping[str, Any]) -> List[Json]:
    plan_ops = _as_list(plan.get("ops"))
    if plan_ops:
        return [dict(op) for op in plan_ops if isinstance(op, Mapping)]
    decision_ops = _as_list(decision_bundle.get("selected_ops"))
    return [dict(op) for op in decision_ops if isinstance(op, Mapping)]


def _plan_success(plan: Mapping[str, Any]) -> bool:
    if not plan:
        return False
    return bool(plan.get("success")) and bool(_as_list(plan.get("ops")))


def _dry_run_safe(dry_run: Mapping[str, Any]) -> bool:
    if not dry_run:
        return False
    if dry_run.get("safe_to_submit") is False:
        return False
    if dry_run.get("success") is False:
        return False
    review = _as_dict(dry_run.get("review"))
    risks = _as_list(review.get("risk_hints"))
    errors = _as_list(dry_run.get("errors"))
    return not risks and not errors


class AgentPipeline:
    """Reason -> Decide -> Plan -> Dry-run orchestrator.

    The class keeps every stage explicit. That makes it easy for a local agent
    or another LLM to inspect exactly where a request was accepted, paused, or
    blocked.
    """

    def run(self, workflow: Mapping[str, Any], request: Mapping[str, Any], *, workflow_name: str = "") -> PipelineResult:
        request_id = _request_id(request)
        policy = PipelinePolicy.from_request(request)
        gates: List[PipelineGate] = []
        trace: List[Json] = []
        warnings: List[str] = []
        errors: List[str] = []

        trace.append({"stage": PipelineStage.NORMALIZE.value, "message": "Normalized pipeline request.", "message_zh": "已规范化流水线请求。", "request_id": request_id})

        memory_context = self._maybe_load_memory_context(workflow, request, workflow_name=workflow_name)
        reasoning_request: Json = dict(request)
        if memory_context:
            reasoning_request["memory_context"] = memory_context
            trace.append({"stage": "memory", "message": "Loaded advisory experience memory.", "message_zh": "已加载经验记忆作为参考证据。", "matches": memory_context.get("returned", 0)})

        try:
            from workflow_reasoner import reason_about_workflow
            reasoning = reason_about_workflow(workflow, reasoning_request)
            reasoning = _as_dict(reasoning)
            gates.append(PipelineGate(PipelineStage.REASON, True, "Reasoner completed.", "Reasoner 已完成。", {"goal": _as_dict(reasoning.get("problem")).get("normalized_goal"), "confidence": reasoning.get("confidence")}))
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            errors.append(f"Reasoner failed: {exc}")
            gates.append(PipelineGate(PipelineStage.REASON, False, "Reasoner failed.", "Reasoner 执行失败。", {"error": str(exc)}))
            return self._finish(request_id, policy, workflow_name, PipelineStatus.ERROR, gates, trace, warnings, errors, reasoning=None)

        try:
            from workflow_decision import decide
            decision_bundle = decide(reasoning, request=request)
            decision_bundle = _as_dict(decision_bundle)
            decision_ok = _decision_status({"decision": decision_bundle}) == "accepted"
            gates.append(PipelineGate(PipelineStage.DECIDE, decision_ok, "Decision Engine completed.", "Decision Engine 已完成。", {"status": decision_bundle.get("status"), "requires_review": decision_bundle.get("requires_review", True)}))
        except Exception as exc:  # pragma: no cover
            errors.append(f"Decision Engine failed: {exc}")
            gates.append(PipelineGate(PipelineStage.DECIDE, False, "Decision Engine failed.", "Decision Engine 执行失败。", {"error": str(exc)}))
            return self._finish(request_id, policy, workflow_name, PipelineStatus.ERROR, gates, trace, warnings, errors, reasoning=reasoning)

        decision_status = str(decision_bundle.get("status") or "").lower()
        if policy.require_accepted_decision and decision_status != "accepted":
            warnings.append("Decision was not accepted; pipeline stopped before planning.")
            return self._finish(
                request_id,
                policy,
                workflow_name,
                PipelineStatus.NEEDS_REVIEW if decision_status == "needs_review" else PipelineStatus.NEEDS_CLARIFICATION,
                gates,
                trace,
                warnings,
                errors,
                reasoning=reasoning,
                decision=decision_bundle,
                memory=memory_context,
                selected_ops=tuple(_as_list(decision_bundle.get("selected_ops"))),
            )

        if not policy.auto_plan:
            warnings.append("auto_plan=false; pipeline stopped after decision.")
            return self._finish(request_id, policy, workflow_name, PipelineStatus.NEEDS_REVIEW, gates, trace, warnings, errors, reasoning=reasoning, decision=decision_bundle, memory=memory_context, selected_ops=tuple(_as_list(decision_bundle.get("selected_ops"))))

        try:
            from workflow_planner import plan_agent_workflow
            planner_request = _planner_request_from_decision(request, reasoning, decision_bundle)
            plan = plan_agent_workflow(workflow, planner_request)
            plan = _as_dict(plan)
            plan_ok = _plan_success(plan)
            gates.append(PipelineGate(PipelineStage.PLAN, plan_ok, "Planner completed.", "Planner 已完成。", {"success": plan.get("success"), "ops": len(_as_list(plan.get("ops")))}))
        except Exception as exc:  # pragma: no cover
            # Fall back to accepted decision ops. This keeps v12 compatible with
            # older local projects whose Planner v2 may not yet be installed.
            plan = {"success": False, "fallback": "decision.selected_ops", "ops": _as_list(decision_bundle.get("selected_ops")), "errors": [str(exc)]}
            warnings.append(f"Planner failed; falling back to decision.selected_ops: {exc}")
            gates.append(PipelineGate(PipelineStage.PLAN, bool(plan["ops"]), "Planner failed; used decision ops fallback.", "Planner 执行失败，已回退到 Decision 选中的 ops。", {"error": str(exc), "ops": len(plan["ops"])}))

        if policy.require_plan_success and not _plan_success(plan):
            warnings.append("Planner did not produce a successful executable plan.")
            return self._finish(request_id, policy, workflow_name, PipelineStatus.BLOCKED, gates, trace, warnings, errors, reasoning=reasoning, decision=decision_bundle, plan=plan, memory=memory_context, selected_ops=tuple(_selected_ops_from_plan_or_decision(plan, decision_bundle)))

        selected_ops = tuple(_selected_ops_from_plan_or_decision(plan, decision_bundle))
        if not policy.auto_dry_run:
            warnings.append("auto_dry_run=false; pipeline stopped after planning.")
            apply_request = self._build_apply_request(request, selected_ops)
            return self._finish(request_id, policy, workflow_name, PipelineStatus.NEEDS_REVIEW, gates, trace, warnings, errors, reasoning=reasoning, decision=decision_bundle, plan=plan, memory=memory_context, selected_ops=selected_ops, apply_request=apply_request)

        try:
            from workflow_self_check import self_check_workflow_edit
            dry_run = self_check_workflow_edit(workflow, ops=list(selected_ops), text=str(request.get("text") or request.get("instruction") or ""), strict=policy.strict)
            dry_run = _as_dict(dry_run)
            if not policy.include_workflow:
                dry_run.pop("workflow_after", None)
            dry_ok = _dry_run_safe(dry_run)
            gates.append(PipelineGate(PipelineStage.DRY_RUN, dry_ok, "Dry-run completed.", "预演已完成。", {"safe_to_submit": dry_run.get("safe_to_submit"), "changed": dry_run.get("changed")}))
        except Exception as exc:  # pragma: no cover
            dry_run = {"success": False, "error": str(exc), "safe_to_submit": False}
            errors.append(f"Dry-run failed: {exc}")
            gates.append(PipelineGate(PipelineStage.DRY_RUN, False, "Dry-run failed.", "预演失败。", {"error": str(exc)}))

        ready = bool(selected_ops) and _dry_run_safe(dry_run)
        apply_request = self._build_apply_request(request, selected_ops) if ready else None
        status = PipelineStatus.READY if ready else PipelineStatus.NEEDS_REVIEW
        return self._finish(request_id, policy, workflow_name, status, gates, trace, warnings, errors, reasoning=reasoning, decision=decision_bundle, plan=plan, dry_run=dry_run, memory=memory_context, selected_ops=selected_ops, ready_to_apply=ready, apply_request=apply_request)


    def _maybe_load_memory_context(self, workflow: Mapping[str, Any], request: Mapping[str, Any], *, workflow_name: str = "") -> Optional[Json]:
        """Load advisory Experience Memory when explicitly enabled.

        Memory is intentionally opt-in. It is useful evidence, but it must not
        silently change pipeline behavior or bypass safety gates.
        """

        memory_cfg = request.get("memory") if isinstance(request.get("memory"), Mapping) else {}
        enabled = bool(memory_cfg.get("enabled") or memory_cfg.get("search") or request.get("use_memory"))
        if not enabled:
            return None
        try:
            from workflow_memory import default_memory_path, memory_context_for_request

            memory_path = memory_cfg.get("memory_path") or request.get("memory_path")
            if not memory_path:
                out_dir = memory_cfg.get("out_dir") or request.get("out_dir")
                memory_path = default_memory_path(out_dir)
            query_request: Json = dict(request)
            query_request.update({k: v for k, v in memory_cfg.items() if k not in {"enabled", "search"}})
            return memory_context_for_request(query_request, memory_path=memory_path, workflow_name=workflow_name, workflow=workflow)
        except Exception as exc:  # pragma: no cover - memory must never break pipeline
            return {
                "success": False,
                "error": str(exc),
                "matches": [],
                "returned": 0,
                "message": "Experience Memory retrieval failed; pipeline continued without memory.",
                "message_zh": "经验记忆检索失败，流水线已在无记忆状态下继续。",
            }

    def _build_apply_request(self, request: Mapping[str, Any], selected_ops: Sequence[Json]) -> Json:
        apply_request: Json = {
            "workflow": request.get("workflow"),
            "ops": [dict(op) for op in selected_ops if isinstance(op, Mapping)],
            "execute": False,
        }
        for key in ("prompt", "negative", "seed", "filename_prefix", "width", "height", "steps"):
            if key in request:
                apply_request[key] = request[key]
        return apply_request

    def _finish(
        self,
        request_id: str,
        policy: PipelinePolicy,
        workflow_name: str,
        status: PipelineStatus,
        gates: Sequence[PipelineGate],
        trace: Sequence[Json],
        warnings: Sequence[str],
        errors: Sequence[str],
        *,
        reasoning: Optional[Json] = None,
        decision: Optional[Json] = None,
        plan: Optional[Json] = None,
        dry_run: Optional[Json] = None,
        memory: Optional[Json] = None,
        selected_ops: Sequence[Json] = (),
        ready_to_apply: bool = False,
        apply_request: Optional[Json] = None,
    ) -> PipelineResult:
        success = status in {PipelineStatus.READY, PipelineStatus.NEEDS_REVIEW, PipelineStatus.NEEDS_CLARIFICATION}
        return PipelineResult(
            success=success,
            request_id=request_id,
            status=status,
            policy=policy,
            workflow_name=workflow_name,
            reasoning=reasoning,
            decision=decision,
            plan=plan,
            dry_run=dry_run,
            memory=memory,
            selected_ops=tuple(dict(op) for op in selected_ops if isinstance(op, Mapping)),
            ready_to_apply=ready_to_apply,
            apply_request=apply_request,
            gates=tuple(gates),
            warnings=tuple(warnings),
            errors=tuple(errors),
            trace=tuple(trace),
        )


def run_agent_pipeline(workflow: Mapping[str, Any], request: Mapping[str, Any], *, workflow_name: str = "") -> Json:
    """Public wrapper for bridge endpoints."""

    return AgentPipeline().run(workflow, request, workflow_name=workflow_name).to_dict()


__all__ = [
    "AgentPipeline",
    "PipelineGate",
    "PipelinePolicy",
    "PipelineResult",
    "PipelineStage",
    "PipelineStatus",
    "run_agent_pipeline",
]
