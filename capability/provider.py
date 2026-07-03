#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider extension helpers for future node packs."""

from __future__ import annotations

from .registry import CapabilityRegistry, CapabilitySpec, default_registry


def register_capability(
    name: str,
    category: str,
    class_keywords: list[str] | tuple[str, ...],
    provider: str = "External",
    priority: int = 50,
    native: bool = False,
    experimental: bool = True,
) -> None:
    """Register a simple external capability in the default registry."""
    default_registry().register(
        CapabilitySpec(
            name=name,
            category=category,
            class_keywords=tuple(class_keywords),
            provider=provider,
            priority=priority,
            native=native,
            experimental=experimental,
        )
    )


def get_registry() -> CapabilityRegistry:
    return default_registry()
