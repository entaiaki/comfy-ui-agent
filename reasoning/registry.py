#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registry utilities for reasoning components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .models import Hypothesis, ProblemSpec

HypothesisFactory = Callable[[ProblemSpec, Mapping[str, object]], Iterable[Hypothesis]]


@dataclass
class HypothesisRegistry:
    """Goal -> hypothesis factory registry.

    It keeps the reasoner open for extension. New problem families can register
    factories without editing the core engine.
    """

    _factories: Dict[str, List[HypothesisFactory]] = field(default_factory=dict)

    def register(self, goal: str, factory: HypothesisFactory) -> None:
        key = str(goal or "").strip()
        if not key:
            raise ValueError("goal must not be empty")
        self._factories.setdefault(key, []).append(factory)

    def factories_for(self, goal: str) -> List[HypothesisFactory]:
        return list(self._factories.get(goal, [])) + list(self._factories.get("*", []))

    def goals(self) -> Tuple[str, ...]:
        return tuple(sorted(self._factories.keys()))
