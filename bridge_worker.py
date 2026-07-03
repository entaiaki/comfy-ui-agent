#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_worker.py

Background worker thread for ComfyUI bridge.

- Consumes tasks from a Queue
- Executes ComfyUI generation via the existing bridge logic
- Updates TaskStore

Standard library only.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from typing import Callable, Dict, Any

from bridge_task_store import TaskStore


class Worker:
    def __init__(
        self,
        name: str,
        q: "queue.Queue[str]",
        store: TaskStore,
        run_task_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        logger,
    ):
        self.name = name
        self.q = q
        self.store = store
        self.run_task_fn = run_task_fn
        self.logger = logger

        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._stop = threading.Event()

    def start(self):
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        self._thread.join(timeout=timeout)

    def _loop(self):
        self.logger.info({"event": "worker_start", "worker": self.name})
        while not self._stop.is_set():
            try:
                task_id = self.q.get(timeout=0.5)
            except queue.Empty:
                continue

            task = self.store.get(task_id)
            if not task:
                self.logger.error({"event": "task_missing", "task_id": task_id})
                self.q.task_done()
                continue

            # If cancel endpoint already marked the task, never start it.
            if task.status == "CANCELLED":
                self.logger.info({"event": "task_skip_cancelled", "task_id": task_id, "request_id": task.request_id})
                self.q.task_done()
                continue

            # cancellation check before starting
            try:
                from bridge_cancel import is_cancelled
                if is_cancelled(task_id):
                    self.store.update(
                        task_id,
                        status="CANCELLED",
                        started_at=None,
                        finished_at=time.time(),
                        error={"type": "Cancelled", "message": "Task was cancelled before execution"},
                    )
                    self.logger.info({"event": "task_cancelled", "task_id": task_id, "request_id": task.request_id})
                    self.q.task_done()
                    continue
            except Exception:
                pass

            self.store.update(task_id, status="RUNNING", started_at=time.time())
            self.logger.info({"event": "task_running", "task_id": task_id, "request_id": task.request_id})

            try:
                # enforce per-task timeout if provided in normalized_request
                timeout_seconds = task.normalized_request.get("task_timeout_seconds")
                if timeout_seconds is None:
                    timeout_seconds = 900

                max_retries = int(task.normalized_request.get("max_retries", 0))
                backoff = float(task.normalized_request.get("retry_backoff_seconds", 1.0))

                from bridge_executor import run_with_timeout
                from bridge_retry import bump_attempt, get_attempt, backoff_sleep

                current_req = dict(task.normalized_request)
                last_exc = None
                for attempt in range(1, max_retries + 2):
                    current_req["__attempt"] = attempt
                    try:
                        self.store.update(task_id, normalized_request={**task.normalized_request, "__attempt": attempt})
                    except Exception:
                        pass
                    try:
                        result = run_with_timeout(self.run_task_fn, current_req, timeout_seconds=int(timeout_seconds))
                        break
                    except Exception as e:
                        last_exc = e
                        self.logger.error({"event": "task_attempt_failed", "task_id": task_id, "attempt": attempt, "err": str(e)})
                        if attempt <= max_retries:
                            backoff_sleep(backoff)
                            continue
                        raise
                finished = time.time()
                self.store.update(task_id, status="SUCCEEDED", finished_at=finished, result=result, error=None)
                try:
                    if task.started_at:
                        from bridge_observability import observe as obs_observe
                        obs_observe("task_latency", finished - task.started_at)
                except Exception:
                    pass
                try:
                    from bridge_metrics import inc
                    inc("tasks_succeeded")
                except Exception:
                    pass
                self.logger.info(
                    {
                        "event": "task_succeeded",
                        "task_id": task_id,
                        "request_id": task.request_id,
                        "result": {"images": result.get("images", [])},
                    }
                )
            except Exception as e:
                finished = time.time()
                self.store.update(
                    task_id,
                    status="FAILED",
                    finished_at=finished,
                    error={"type": type(e).__name__, "message": str(e), "traceback": traceback.format_exc()},
                )
                try:
                    if task.started_at:
                        from bridge_observability import observe as obs_observe
                        obs_observe("task_latency", finished - task.started_at)
                except Exception:
                    pass
                try:
                    from bridge_metrics import inc
                    inc("tasks_failed")
                except Exception:
                    pass
                self.logger.error({"event": "task_failed", "task_id": task_id, "request_id": task.request_id, "err": str(e)})
            finally:
                self.q.task_done()
