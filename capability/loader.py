#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Loader facade kept for future YAML/JSON capability provider packs."""

from __future__ import annotations

from .registry import CapabilityRegistry, default_registry


def load_default_registry() -> CapabilityRegistry:
    return default_registry()
