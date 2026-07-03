#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_logging.py

Simple JSON logger (stdout) for the local bridge.
Standard library only.

Usage:
    log = get_logger()
    log.info({"event": "something", ...})

"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict


class JsonLogger:
    def __init__(self, name: str = "bridge"):
        self.name = name

    def _emit(self, level: str, obj: Dict[str, Any]):
        payload = dict(obj)
        payload.update({"level": level, "logger": self.name, "ts": time.time()})
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def info(self, obj: Dict[str, Any]):
        self._emit("INFO", obj)

    def error(self, obj: Dict[str, Any]):
        self._emit("ERROR", obj)


def get_logger(name: str = "bridge") -> JsonLogger:
    return JsonLogger(name=name)
