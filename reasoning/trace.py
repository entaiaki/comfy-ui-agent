#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trace builder for explainable reasoning."""

from __future__ import annotations

from typing import Any, Dict, List

from .models import Json, ReasoningTraceStep


class TraceBuilder:
    """Small helper that keeps trace construction consistent."""

    def __init__(self) -> None:
        self._steps: List[ReasoningTraceStep] = []

    def add(self, stage: str, message: str, message_zh: str = "", **data: Any) -> None:
        payload: Json = dict(data) if data else {}
        self._steps.append(ReasoningTraceStep(stage=stage, message=message, message_zh=message_zh, data=payload))

    def extend(self, steps: List[ReasoningTraceStep]) -> None:
        self._steps.extend(steps)

    def build(self) -> List[ReasoningTraceStep]:
        return list(self._steps)
