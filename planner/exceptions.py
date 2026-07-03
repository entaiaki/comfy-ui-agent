#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Planner exceptions."""

class PlannerEngineError(ValueError):
    """Raised when the planner cannot build a safe candidate."""


class UnsupportedPlanningGoal(PlannerEngineError):
    """Raised when no planner strategy supports the requested goal."""
