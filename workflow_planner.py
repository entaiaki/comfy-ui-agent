#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility entry for Planner Engine 2.0."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from planner import PlannerEngine, plan_workflow

Json = Dict[str, Any]


def plan_agent_workflow(workflow: Mapping[str, Any], request: Mapping[str, Any]) -> Json:
    """Plan workflow edits using Planner Engine 2.0."""
    return plan_workflow(workflow, request)
