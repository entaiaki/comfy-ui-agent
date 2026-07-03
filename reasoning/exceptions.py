#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exceptions for reasoning package."""

from __future__ import annotations


class ReasoningError(ValueError):
    """Base class for deterministic reasoning errors."""


class UnsupportedReasoningRequest(ReasoningError):
    """Raised when the user goal cannot be interpreted safely."""
