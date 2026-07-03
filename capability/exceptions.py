#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capability Registry exceptions."""


class CapabilityError(RuntimeError):
    """Base error for capability registry failures."""


class CapabilityDetectionError(CapabilityError):
    """Raised when workflow capability detection fails."""


class CapabilityCacheError(CapabilityError):
    """Raised when manifest cache cannot be read or written."""
