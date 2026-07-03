#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_knowledge.py

Compatibility wrapper for v13 Knowledge YAML Engine.

Older modules may import workflow_knowledge. New code should prefer
workflow_knowledge_engine, but this wrapper keeps the project stable.
"""

from workflow_knowledge_engine import *  # noqa: F401,F403
