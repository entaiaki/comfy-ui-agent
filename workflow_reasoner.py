#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_reasoner.py

Compatibility wrapper for the P8 reasoning package.

Use this from bridge code:
    from workflow_reasoner import reason_about_workflow

The real implementation lives in reasoning/ so the core can grow without
turning this file into a large monolith.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from reasoning.reasoner import WorkflowReasoner, reason_about_workflow

Json = Dict[str, Any]


def reason(workflow: Mapping[str, Any], request: Mapping[str, Any]) -> Json:
    """Alias kept for simple local-agent calls."""
    return reason_about_workflow(workflow, request)


__all__ = ["WorkflowReasoner", "reason_about_workflow", "reason"]
