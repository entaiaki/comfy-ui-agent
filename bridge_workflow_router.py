#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_workflow_router.py

Workflow routing helpers.

Supports routing by "profile" (quality/speed/etc.) to different workflow json files.

This enables a resume-friendly "config-driven multi-workflow routing" story.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class WorkflowRouter:
    base_dir: Path
    profile_map: Dict[str, str]
    default_profile: str = "default"

    def resolve(self, profile: Optional[str], workflow_override: Optional[str] = None) -> Path:
        if workflow_override:
            p = Path(workflow_override)
            return p if p.is_absolute() else (self.base_dir / p)

        prof = (profile or self.default_profile).strip()
        wf = self.profile_map.get(prof) or self.profile_map.get(self.default_profile)
        if not wf:
            raise FileNotFoundError(f"No workflow configured for profile: {prof}")
        return (self.base_dir / wf)
