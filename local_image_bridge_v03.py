#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
local_image_bridge_v03.py

Robust local HTTP bridge for ComfyUI image generation.

Compared with v02, this version is more tolerant of QwenPaw output.

It accepts:
1. Pure flat JSON.
2. Pure nested QwenPaw JSON.
3. Markdown-wrapped JSON, for example:

## Bridge Request JSON

```json
{
  ...
}
```

4. Text that contains one JSON object somewhere inside it.

Endpoint:
POST http://127.0.0.1:7861/generate-image
"""

import argparse
import json
import random
import re
import sys
import time
import uuid
import traceback
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from bridge_logging import get_logger
from bridge_task_store import TaskStore
from bridge_worker import Worker
from bridge_security import ApiKeyAuth, RateLimiter
from bridge_workflow_router import WorkflowRouter
from bridge_observability import observe as obs_observe, snapshot as obs_snapshot, to_prometheus
from bridge_policy import load_policy
from workflow_ops import apply_ops, WorkflowOpsError
from workflow_inspect import inspect_workflow, inspect_registry
from workflow_resolver import NodeResolver
from workflow_validator import validate_workflow
from workflow_rule_planner import plan_workflow_edit
from workflow_self_check import self_check_workflow_edit
from workflow_agent_service import agent_plan, agent_dry_run, agent_prepare_apply
from workflow_reasoner import reason_about_workflow
from workflow_decision import decide_workflow_action
from workflow_agent_pipeline import run_agent_pipeline
from workflow_planner import plan_agent_workflow
from workflow_memory import default_memory_path, memory_stats, remember_experience, search_memory
from workflow_knowledge_engine import knowledge_manifest, query_knowledge
from workflow_capability import (
    capability_registry_manifest,
    get_capability_manifest,
    query_workflow_capabilities,
    refresh_capability_manifest,
    summarize_workflow_capabilities,
)
from workflow_semantics import inspect_semantics, summarize_semantics
from workflow_semantic_resolver import list_semantic_targets, resolve_many_semantic_targets, resolve_semantic_target


DEFAULT_COMFYUI_SERVER = "http://127.0.0.1:8186"
DEFAULT_WORKFLOW = "flux_kontext_txt2img_api_workflow.json"
DEFAULT_OUT_DIR = "generated_outputs"

DEFAULT_PROMPT = (
    "A premium commercial image, clean composition, soft studio lighting, realistic shadows, "
    "high-end visual style, sharp details, coherent image."
)

DEFAULT_NEGATIVE = (
    "cluttered background, warped structure, wrong proportions, messy random objects, "
    "harsh dark lighting, low-resolution texture, watermark, random text, unrealistic perspective, "
    "overexposure, distracting elements"
)


class BridgeConfig:
    def __init__(self, comfyui_server, workflow_path, out_dir, timeout_seconds, poll_interval):
        self.comfyui_server = comfyui_server.rstrip("/")
        self.workflow_path = Path(workflow_path)
        self.out_dir = Path(out_dir)
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval


CONFIG = None
LOGGER = get_logger("local_image_bridge")
TASK_STORE = None  # type: TaskStore | None
TASK_QUEUE = None  # type: queue.Queue | None
WORKERS = []       # type: list[Worker]
AUTH = None        # type: ApiKeyAuth | None
RATE_LIMITER = None  # type: RateLimiter | None
ROUTER = None      # type: WorkflowRouter | None
POLICY = None      # type: object | None
BRIDGE_VERSION = "0.3-p15"
# metrics are stored in bridge_metrics.py to avoid circular imports


def read_json_file(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def extract_first_json_object(text: str) -> str:
    """
    Extract the first balanced JSON object from arbitrary text.
    This allows users to paste QwenPaw's whole markdown answer.
    """
    text = text.strip()

    # Remove common markdown fences, but keep inner content.
    text = text.replace("```json", "```")
    text = text.replace("```JSON", "```")

    # If fenced, prefer content inside the first fenced block containing a JSON object.
    fence_blocks = re.findall(r"```(?:\w+)?\s*(.*?)```", text, flags=re.DOTALL)
    for block in fence_blocks:
        block = block.strip()
        if "{" in block and "}" in block:
            text = block
            break

    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in request body.")

    in_string = False
    escape = False
    depth = 0

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    raise ValueError("Found '{' but could not find a balanced closing '}'.")


def parse_request_body(raw_body: str) -> dict:
    """
    Parse either pure JSON or markdown/text containing JSON.
    """
    raw_body = raw_body.strip()
    if not raw_body:
        return {}

    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        json_text = extract_first_json_object(raw_body)
        return json.loads(json_text)


def http_json(method: str, url: str, payload=None, timeout: int = 30) -> dict:
    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} error from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot connect to {url}\nReason: {e}") from e


def http_bytes(url: str, timeout: int = 120) -> bytes:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} error from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot download from {url}\nReason: {e}") from e


def get_nested(data: dict, path: list, default=None):
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        if key not in cur:
            return default
        cur = cur[key]
    return cur


def first_existing(*values, default=None):
    for value in values:
        if value is not None:
            return value
    return default


def to_int(value, default):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def sanitize_filename_prefix(prefix: str) -> str:
    if not prefix:
        return "Flux_Bridge"
    prefix = str(prefix).strip()
    prefix = prefix.replace("-", "_").replace(" ", "_")
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", prefix)
    prefix = re.sub(r"_+", "_", prefix).strip("_")
    if not prefix:
        prefix = "Flux_Bridge"
    return prefix[:80]


def normalize_request(request_data: dict) -> dict:
    prompt_field = request_data.get("prompt")

    if isinstance(prompt_field, dict):
        prompt = first_existing(
            prompt_field.get("positive"),
            prompt_field.get("prompt"),
            prompt_field.get("text"),
            default=DEFAULT_PROMPT,
        )
        negative = first_existing(
            request_data.get("negative"),
            prompt_field.get("negative"),
            default=DEFAULT_NEGATIVE,
        )
    else:
        prompt = first_existing(
            request_data.get("prompt"),
            get_nested(request_data, ["prompt", "positive"]),
            default=DEFAULT_PROMPT,
        )
        negative = first_existing(
            request_data.get("negative"),
            get_nested(request_data, ["prompt", "negative"]),
            default=DEFAULT_NEGATIVE,
        )

    width = to_int(
        first_existing(request_data.get("width"), get_nested(request_data, ["generation", "width"]), default=1024),
        1024,
    )

    height = to_int(
        first_existing(request_data.get("height"), get_nested(request_data, ["generation", "height"]), default=1280),
        1280,
    )

    steps = to_int(
        first_existing(request_data.get("steps"), get_nested(request_data, ["generation", "steps"]), default=24),
        24,
    )

    seed = to_int(
        first_existing(request_data.get("seed"), get_nested(request_data, ["generation", "seed"]), default=-1),
        -1,
    )

    filename_prefix = first_existing(
        request_data.get("filename_prefix"),
        get_nested(request_data, ["output", "filename_prefix"]),
        get_nested(request_data, ["task", "description"]),
        default="Flux_Bridge",
    )

    workflow = first_existing(
        request_data.get("workflow"),
        get_nested(request_data, ["model", "workflow"]),
        default=str(CONFIG.workflow_path),
    )

    workflow = str(workflow)
    if not workflow.lower().endswith(".json"):
        workflow = str(CONFIG.workflow_path)

    # Always use tested output directory unless explicitly passed by top-level out_dir.
    out_dir = request_data.get("out_dir", str(CONFIG.out_dir))

    comfyui_server = first_existing(
        request_data.get("comfyui_server"),
        get_nested(request_data, ["model", "server"]),
        default=CONFIG.comfyui_server,
    )

    return {
        "prompt": str(prompt),
        "negative": str(negative),
        "width": width,
        "height": height,
        "steps": steps,
        "seed": seed,
        "filename_prefix": sanitize_filename_prefix(filename_prefix),
        "workflow": workflow,
        "out_dir": str(out_dir),
        "comfyui_server": str(comfyui_server).rstrip("/"),
        # P2 stability controls: preserve user-provided values.
        # Policy defaults are applied later only when these are None.
        "task_timeout_seconds": request_data.get("task_timeout_seconds"),
        "max_retries": request_data.get("max_retries"),
        "retry_backoff_seconds": request_data.get("retry_backoff_seconds"),
    }


def set_if_exists(workflow: dict, node_id: str, input_name: str, value):
    node = workflow.get(str(node_id))
    if not node:
        return
    inputs = node.setdefault("inputs", {})
    inputs[input_name] = value


def patch_workflow(workflow: dict, req: dict):
    set_if_exists(workflow, "5", "text", req["prompt"])
    set_if_exists(workflow, "7", "text", req["negative"])

    set_if_exists(workflow, "9", "seed", req["seed"])
    set_if_exists(workflow, "9", "steps", req["steps"])

    set_if_exists(workflow, "4", "width", req["width"])
    set_if_exists(workflow, "4", "height", req["height"])

    set_if_exists(workflow, "8", "width", req["width"])
    set_if_exists(workflow, "8", "height", req["height"])

    set_if_exists(workflow, "11", "filename_prefix", req["filename_prefix"])

    return workflow


def submit_prompt(comfyui_server: str, workflow: dict) -> str:
    url = comfyui_server.rstrip("/") + "/prompt"

    # Some exported API workflows include a wrapper like {"prompt": {...}}.
    # The ComfyUI /prompt endpoint expects the *inner* prompt dict.
    if isinstance(workflow, dict) and isinstance(workflow.get("prompt"), dict):
        workflow = workflow["prompt"]

    payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}

    result = http_json("POST", url, payload=payload, timeout=30)

    if "node_errors" in result and result["node_errors"]:
        raise RuntimeError(
            "ComfyUI rejected the workflow with node_errors:\n"
            + json.dumps(result["node_errors"], ensure_ascii=False, indent=2)
        )

    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(
            "ComfyUI did not return prompt_id:\n"
            + json.dumps(result, ensure_ascii=False, indent=2)
        )

    return prompt_id


def wait_for_history(comfyui_server: str, prompt_id: str, timeout_seconds: int, poll_interval: float) -> dict:
    url = comfyui_server.rstrip("/") + f"/history/{prompt_id}"
    start = time.time()

    while True:
        result = http_json("GET", url, timeout=30)

        if prompt_id in result:
            history = result[prompt_id]
            outputs = history.get("outputs", {})
            status = history.get("status", {})

            if outputs:
                return history
            if status.get("completed") is True:
                return history

        if time.time() - start > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for prompt_id={prompt_id}")

        time.sleep(poll_interval)


def extract_images_from_history(history: dict) -> list:
    images = []
    outputs = history.get("outputs", {})

    for node_id, node_output in outputs.items():
        for image in node_output.get("images", []):
            images.append(
                {
                    "node_id": node_id,
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
            )

    return images


def sha256_file(path: Path) -> str:
    """Return SHA-256 hash for a file. Used for reproducibility audit."""
    import hashlib

    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_audit_fields(normalized: dict) -> dict:
    """Build best-effort audit metadata without breaking task submission."""
    workflow_path = Path(str(normalized.get("workflow") or ""))
    comfyui_server = str(normalized.get("comfyui_server") or "").rstrip("/")

    audit = {
        "bridge_version": BRIDGE_VERSION,
        "workflow_path": str(workflow_path),
        "workflow_sha256": None,
        "comfyui_server": comfyui_server,
        "comfyui_version": "unknown",
    }

    try:
        if workflow_path.exists():
            audit["workflow_sha256"] = sha256_file(workflow_path)
    except Exception as exc:
        audit["workflow_sha256_error"] = str(exc)

    try:
        if comfyui_server:
            audit["comfyui_version"] = check_comfyui(comfyui_server)
    except Exception as exc:
        audit["comfyui_version_error"] = str(exc)

    return audit


def safe_filename(name: str) -> str:
    bad_chars = '<>:"/\\|?*'
    for ch in bad_chars:
        name = name.replace(ch, "_")
    return name


def download_images(comfyui_server: str, images: list, out_dir: Path) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []

    for image in images:
        query = urlencode(
            {
                "filename": image["filename"],
                "subfolder": image["subfolder"],
                "type": image["type"],
            }
        )
        url = comfyui_server.rstrip("/") + "/view?" + query

        data = http_bytes(url, timeout=120)

        filename = safe_filename(image["filename"] or f"comfyui_{int(time.time())}.png")
        save_path = out_dir / filename
        save_path.write_bytes(data)
        saved_paths.append(str(save_path.resolve()))

    return saved_paths


def generate_image(request_data: dict) -> dict:
    req = normalize_request(request_data)

    if req["seed"] < 0:
        req["seed"] = random.randint(0, 2**63 - 1)

    workflow_path = Path(req["workflow"])
    out_dir = Path(req["out_dir"])
    comfyui_server = req["comfyui_server"]

    workflow = read_json_file(workflow_path)
    workflow = patch_workflow(workflow, req)

    # Optional graph edit ops (AI-controlled). Applied after basic patching.
    ops = request_data.get("ops")
    if ops is not None:
        if not isinstance(ops, list):
            raise ValueError("ops must be a list")
        try:
            workflow = apply_ops(workflow, ops)
        except WorkflowOpsError as exc:
            raise ValueError(f"Invalid ops: {exc}")

    out_dir.mkdir(parents=True, exist_ok=True)
    debug_path = out_dir / "bridge_last_submitted_workflow.json"
    debug_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    prompt_id = submit_prompt(comfyui_server, workflow)

    history = wait_for_history(
        comfyui_server=comfyui_server,
        prompt_id=prompt_id,
        timeout_seconds=CONFIG.timeout_seconds,
        poll_interval=CONFIG.poll_interval,
    )

    images = extract_images_from_history(history)
    if not images:
        raise RuntimeError(
            "Generation completed but no output images were found in history:\n"
            + json.dumps(history, ensure_ascii=False, indent=2)
        )

    image_paths = download_images(comfyui_server, images, out_dir)

    return {
        "success": True,
        "prompt_id": prompt_id,
        "seed": req["seed"],
        "width": req["width"],
        "height": req["height"],
        "steps": req["steps"],
        "filename_prefix": req["filename_prefix"],
        "image_paths": image_paths,
        "debug_workflow": str(debug_path.resolve()),
        "normalized_request": {
            "prompt": req["prompt"],
            "negative": req["negative"],
            "width": req["width"],
            "height": req["height"],
            "steps": req["steps"],
            "seed": req["seed"],
            "filename_prefix": req["filename_prefix"],
        },
    }


class ImageBridgeHandler(BaseHTTPRequestHandler):
    server_version = "LocalImageBridge/0.3"

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"success": True})

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ["/", "/health"]:
            self._send_json(
                200,
                {
                    "success": True,
                    "service": "local_image_bridge",
                    "version": "0.3",
                    "message": "Bridge is running.",
                    "supports": [
                        "flat_json",
                        "nested_qwenpaw_json",
                        "markdown_wrapped_json",
                        "raw_text_containing_json",
                    ],
                    "endpoints": {
                        "health": "GET /health",
                        "generate_image": "POST /generate-image",
                        "submit": "POST /submit",
                        "task": "GET /task/{task_id}",
                        "metrics": "GET /metrics",
                        "workflow_inspect": "GET /workflow/inspect?workflow=xxx.json",
                            "workflow_semantics": "GET /workflow/semantics?workflow=xxx.json",
                            "workflow_semantic_targets": "GET /workflow/semantic-targets?workflow=xxx.json",
                            "workflow_semantic_resolve": "POST /workflow/semantic-resolve",
                        "workflow_resolve": "POST /workflow/resolve",
                        "workflow_validate": "POST /workflow/validate",
                        "workflow_registry": "GET /workflow/registry",
                        "workflow_plan": "POST /workflow/plan",
                        "workflow_plan_and_dry_run": "POST /workflow/plan-and-dry-run",
                        "agent_plan": "POST /agent/plan",
                        "agent_dry_run": "POST /agent/dry-run",
                        "agent_apply": "POST /agent/apply",
                        "agent_reason": "POST /agent/reason",
                        "agent_decide": "POST /agent/decide",
                        "agent_pipeline": "POST /agent/pipeline",
                        "agent_plan_v2": "POST /agent/plan-v2",
                        "agent_memory_log": "POST /agent/memory/log",
                        "agent_memory_search": "POST /agent/memory/search",
                        "agent_memory_stats": "GET /agent/memory/stats",
                        "agent_knowledge_manifest": "GET /agent/knowledge/manifest",
                        "agent_knowledge_query": "POST /agent/knowledge/query",
                        "agent_capabilities": "GET /agent/capabilities?workflow=xxx.json",
                        "agent_capabilities_manifest": "GET /agent/capabilities/manifest?workflow=xxx.json",
                        "agent_capabilities_registry": "GET /agent/capabilities/registry",
                        "agent_capabilities_refresh": "POST /agent/capabilities/refresh",
                        "agent_capabilities_query": "POST /agent/capabilities/query",
                    },
                    "comfyui_server": CONFIG.comfyui_server,
                    "workflow": str(CONFIG.workflow_path),
                    "out_dir": str(CONFIG.out_dir),
                },
            )
            return

        if path == "/metrics":
            # content negotiation: json or prometheus text
            qsize = TASK_QUEUE.qsize() if TASK_QUEUE else None
            metrics = __import__("bridge_metrics").snapshot()
            hist = obs_snapshot()

            accept = (self.headers.get("Accept") or "").lower()
            if "text/plain" in accept or "prometheus" in accept:
                payload = to_prometheus(metrics=metrics, histograms=hist, extra={"queue_size": qsize})
                body = payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return

            self._send_json(
                200,
                {
                    "success": True,
                    "queue_size": qsize,
                    "metrics": metrics,
                    "histograms": hist,
                    "rate_limit": {
                        "enabled": True,
                        "capacity": RATE_LIMITER.capacity if RATE_LIMITER else None,
                        "refill_rate_per_sec": RATE_LIMITER.refill_rate_per_sec if RATE_LIMITER else None,
                    },
                    "auth": {"enabled": AUTH.enabled() if AUTH else False},
                },
            )
            return


        if path == "/agent/knowledge/manifest":
            try:
                qs = urlparse(self.path).query
                params = {}
                for part in qs.split("&"):
                    if not part:
                        continue
                    if "=" in part:
                        k, v = part.split("=", 1)
                        try:
                            from urllib.parse import unquote
                            v = unquote(v)
                        except Exception:
                            pass
                        params[k] = v
                self._send_json(200, knowledge_manifest(params))
            except Exception as exc:
                self._send_json(400, {"success": False, "error": str(exc), "path": path})
            return


        if path in ["/agent/capabilities", "/agent/capabilities/manifest", "/agent/capabilities/registry"]:
            try:
                qs = urlparse(self.path).query
                params = {}
                for part in qs.split("&"):
                    if not part:
                        continue
                    if "=" in part:
                        k, v = part.split("=", 1)
                        try:
                            from urllib.parse import unquote
                            v = unquote(v)
                        except Exception:
                            pass
                        params[k] = v
                if path == "/agent/capabilities/registry":
                    self._send_json(200, capability_registry_manifest())
                    return
                wf_param = params.get("workflow")
                wf_path = Path(wf_param) if wf_param else CONFIG.workflow_path
                data = read_json_file(Path(wf_path))
                cache_dir = params.get("cache_dir") or str(Path(CONFIG.out_dir) / "capability_cache")
                refresh = str(params.get("refresh") or "0").lower() in ["1", "true", "yes", "y"]
                if path == "/agent/capabilities":
                    summary = summarize_workflow_capabilities(data, workflow_name=str(wf_path), cache_dir=cache_dir)
                    self._send_json(200, {"success": True, "workflow": str(wf_path), "capabilities": summary})
                    return
                manifest = get_capability_manifest(data, workflow_name=str(wf_path), cache_dir=cache_dir, refresh=refresh)
                self._send_json(200, {"success": True, "workflow": str(wf_path), "manifest": manifest})
            except Exception as exc:
                self._send_json(400, {"success": False, "error": str(exc), "path": path})
            return

        if path == "/agent/memory/stats":
            try:
                qs = urlparse(self.path).query
                params = {}
                for part in qs.split("&"):
                    if not part:
                        continue
                    if "=" in part:
                        k, v = part.split("=", 1)
                        try:
                            from urllib.parse import unquote
                            v = unquote(v)
                        except Exception:
                            pass
                        params[k] = v
                mem_path = params.get("memory_path") or str(default_memory_path(CONFIG.out_dir))
                self._send_json(200, memory_stats(memory_path=mem_path))
            except Exception as exc:
                self._send_json(400, {"success": False, "error": str(exc), "path": path})
            return

        if path.startswith("/task/"):
            # allow /task/{id} only (POST cancel handled elsewhere)
            task_id = path.split("/task/", 1)[1].strip().split("/", 1)[0]
            if not task_id:
                self._send_json(400, {"success": False, "error": "Missing task_id"})
                return
            if not TASK_STORE:
                self._send_json(500, {"success": False, "error": "Task store not initialized"})
                return
            task = TASK_STORE.get(task_id)
            if not task:
                self._send_json(404, {"success": False, "error": f"Task not found: {task_id}"})
                return
            # return task with computed latency fields
            data = {
                "success": True,
                "task": {
                    "task_id": task.task_id,
                    "request_id": task.request_id,
                    "status": task.status,
                    "created_at": task.created_at,
                    "started_at": task.started_at,
                    "finished_at": task.finished_at,
                    "latency_seconds": (task.finished_at - task.started_at) if (task.finished_at and task.started_at) else None,
                    "normalized_request": task.normalized_request,
                    "result": task.result,
                    "error": task.error,
                },
            }
            self._send_json(200, data)
            return

        if path == "/workflow/inspect":
            # Query param: workflow=path/to.json (optional). Defaults to CONFIG.workflow_path.
            qs = urlparse(self.path).query
            params = {}
            try:
                for part in qs.split("&"):
                    if not part:
                        continue
                    if "=" in part:
                        k, v = part.split("=", 1)
                        try:
                            from urllib.parse import unquote
                            v = unquote(v)
                        except Exception:
                            pass
                        params[k] = v
            except Exception:
                params = {}

            wf_param = params.get("workflow")
            wf_path = Path(wf_param) if wf_param else CONFIG.workflow_path
            try:
                data = read_json_file(Path(wf_path))
                summary = inspect_workflow(data)
                self._send_json(200, {"success": True, "workflow": str(wf_path), "summary": summary})
            except Exception as exc:
                self._send_json(400, {"success": False, "error": str(exc), "workflow": str(wf_path)})
            return

        if path == "/workflow/semantics":
            # Query params:
            # - workflow=path/to.json (optional). Defaults to CONFIG.workflow_path.
            # - full=1 to include every SemanticNode. Default returns compact summary.
            qs = urlparse(self.path).query
            params = {}
            try:
                for part in qs.split("&"):
                    if not part:
                        continue
                    if "=" in part:
                        k, v = part.split("=", 1)
                        try:
                            from urllib.parse import unquote
                            v = unquote(v)
                        except Exception:
                            pass
                        params[k] = v
            except Exception:
                params = {}

            wf_param = params.get("workflow")
            wf_path = Path(wf_param) if wf_param else CONFIG.workflow_path
            include_nodes = str(params.get("full") or params.get("include_nodes") or "0").lower() in ["1", "true", "yes", "y"]
            try:
                data = read_json_file(Path(wf_path))
                summary = inspect_semantics(data, include_nodes=include_nodes) if include_nodes else summarize_semantics(data)
                self._send_json(200, {"success": True, "workflow": str(wf_path), "semantics": summary})
            except Exception as exc:
                self._send_json(400, {"success": False, "error": str(exc), "workflow": str(wf_path)})
            return

        if path == "/workflow/registry":
            self._send_json(200, inspect_registry())
            return

        self._send_json(404, {"success": False, "error": f"Unknown GET path: {path}"})

    def do_POST(self):
        path = urlparse(self.path).path

        # Auth + rate limit
        api_key = self.headers.get("X-API-Key")
        if AUTH and not AUTH.check(api_key):
            self._send_json(401, {"success": False, "error": "Unauthorized"})
            return

        rate_key = api_key or "anonymous"
        if RATE_LIMITER:
            allowed, info = RATE_LIMITER.allow(rate_key, cost=1.0)
            if not allowed:
                self._send_json(429, {"success": False, "error": "Rate limit exceeded", "rate_limit": info})
                return

        if path in ["/agent/plan", "/agent/plan-v2", "/agent/dry-run", "/agent/apply", "/agent/reason", "/agent/decide", "/agent/pipeline", "/agent/memory/log", "/agent/memory/search", "/agent/knowledge/query", "/agent/capabilities/refresh", "/agent/capabilities/query", "/workflow/resolve", "/workflow/semantic-resolve", "/workflow/validate", "/workflow/plan", "/workflow/plan-and-dry-run", "/workflow/dry-run", "/workflow/self-check"]:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length).decode("utf-8-sig") if content_length > 0 else "{}"
                request_data = parse_request_body(raw_body)
                wf_path = Path(request_data.get("workflow") or CONFIG.workflow_path)
                data = read_json_file(wf_path)

                if path == "/agent/knowledge/query":
                    result = query_knowledge(request_data)
                    self._send_json(200, result)
                    return

                if path == "/agent/capabilities/refresh":
                    cache_dir = request_data.get("cache_dir") or str(Path(CONFIG.out_dir) / "capability_cache")
                    result = refresh_capability_manifest(data, workflow_name=str(wf_path), cache_dir=cache_dir)
                    self._send_json(200, {"success": True, "workflow": str(wf_path), "manifest": result})
                    return

                if path == "/agent/capabilities/query":
                    cache_dir = request_data.get("cache_dir") or str(Path(CONFIG.out_dir) / "capability_cache")
                    result = query_workflow_capabilities(data, request_data, workflow_name=str(wf_path), cache_dir=cache_dir)
                    self._send_json(200, result)
                    return

                if path == "/agent/memory/log":
                    mem_path = request_data.get("memory_path") or str(default_memory_path(CONFIG.out_dir))
                    result = remember_experience(request_data, memory_path=mem_path, workflow_name=str(wf_path))
                    self._send_json(200, result)
                    return

                if path == "/agent/memory/search":
                    mem_path = request_data.get("memory_path") or str(default_memory_path(CONFIG.out_dir))
                    result = search_memory(request_data, memory_path=mem_path)
                    self._send_json(200, result)
                    return

                if path == "/agent/plan":
                    result = agent_plan(data, request_data, workflow_name=str(wf_path))
                    self._send_json(200, result)
                    return

                if path == "/agent/plan-v2":
                    result = plan_agent_workflow(data, request_data)
                    self._send_json(200, {"success": bool(result.get("success")), "workflow": str(wf_path), "planner_version": "2.0", **result})
                    return

                if path == "/agent/dry-run":
                    result = agent_dry_run(data, request_data, workflow_name=str(wf_path))
                    self._send_json(200, result)
                    return

                if path == "/agent/reason":
                    result = reason_about_workflow(data, request_data)
                    self._send_json(200, result)
                    return

                if path == "/agent/decide":
                    result = decide_workflow_action(data, request_data)
                    self._send_json(200, result)
                    return

                if path == "/agent/pipeline":
                    result = run_agent_pipeline(data, request_data, workflow_name=str(wf_path))
                    self._send_json(200, result)
                    return

                if path == "/agent/apply":
                    prepared = agent_prepare_apply(data, request_data, workflow_name=str(wf_path))
                    execute = bool(request_data.get("execute", True))
                    if not execute:
                        self._send_json(200, prepared)
                        return
                    result = generate_image(prepared["generation_request"])
                    result["agent"] = {
                        "request_id": prepared.get("request_id"),
                        "workflow": prepared.get("workflow"),
                        "plan": prepared.get("plan"),
                        "ops": prepared.get("ops"),
                        "dry_run_review": prepared.get("dry_run", {}).get("review", {}),
                    }
                    self._send_json(200, result)
                    return

                if path == "/workflow/resolve":
                    query = request_data.get("query") or request_data.get("node") or {}
                    limit = int(request_data.get("limit", 20))
                    candidates = NodeResolver(data).find(query, limit=limit)
                    self._send_json(200, {"success": True, "workflow": str(wf_path), "query": query, "candidates": candidates})
                    return

                if path == "/workflow/semantic-resolve":
                    if "targets" in request_data:
                        targets = request_data.get("targets") or []
                        resolved = resolve_many_semantic_targets(data, targets)
                    else:
                        target = request_data.get("target") or request_data.get("query")
                        resolved = resolve_semantic_target(data, target).to_dict()
                    self._send_json(200, {"success": True, "workflow": str(wf_path), "resolved": resolved})
                    return

                if path == "/workflow/validate":
                    report = validate_workflow(data, strict=bool(request_data.get("strict", False)))
                    self._send_json(200, {"success": True, "workflow": str(wf_path), "validation": report})
                    return

                if path == "/workflow/plan":
                    plan = plan_workflow_edit(request_data)
                    self._send_json(200, {"success": True, "workflow": str(wf_path), "plan": plan, "ops": plan.get("ops", [])})
                    return

                if path == "/workflow/plan-and-dry-run":
                    plan = plan_workflow_edit(request_data)
                    strict = bool(request_data.get("strict", False))
                    include_workflow = bool(request_data.get("include_workflow", False))
                    report = self_check_workflow_edit(data, ops=plan.get("ops", []), text="", strict=strict)
                    report["workflow"] = str(wf_path)
                    report["plan"] = plan
                    if not include_workflow:
                        report.pop("workflow_after", None)
                    self._send_json(200, report)
                    return

                if path in ["/workflow/dry-run", "/workflow/self-check"]:
                    text = str(request_data.get("text") or request_data.get("instruction") or "")
                    ops = request_data.get("ops")
                    strict = bool(request_data.get("strict", False))
                    include_workflow = bool(request_data.get("include_workflow", False))
                    report = self_check_workflow_edit(data, ops=ops, text=text, strict=strict)
                    report["workflow"] = str(wf_path)
                    if not include_workflow:
                        report.pop("workflow_after", None)
                    self._send_json(200, report)
                    return

            except Exception as exc:
                self._send_json(400, {"success": False, "error": str(exc), "path": path})
                return

        # Synchronous endpoint (kept for backward compatibility)
        if path == "/generate-image":
            from bridge_metrics import inc
            inc("sync_total")
            request_id = self.headers.get("X-Request-Id") or str(uuid.uuid4())
            start = time.time()
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length).decode("utf-8-sig") if content_length > 0 else "{}"
                request_data = parse_request_body(raw_body)

                LOGGER.info({"event": "sync_request", "request_id": request_id, "path": path})

                result = generate_image(request_data)
                result["request_id"] = request_id
                latency = time.time() - start
                result["latency_seconds"] = latency
                obs_observe("sync_latency", latency)
                self._send_json(200, result)

            except Exception as exc:
                LOGGER.error({"event": "sync_error", "request_id": request_id, "err": str(exc)})
                self._send_json(
                    500,
                    {
                        "success": False,
                        "request_id": request_id,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                        "latency_seconds": time.time() - start,
                    },
                )
            return

        # Cancel endpoint
        if path.startswith("/task/") and path.endswith("/cancel"):
            request_id = self.headers.get("X-Request-Id") or str(uuid.uuid4())
            task_id = path.split("/task/", 1)[1].split("/cancel", 1)[0].strip()
            if not task_id:
                self._send_json(400, {"success": False, "request_id": request_id, "error": "Missing task_id"})
                return
            if not TASK_STORE:
                self._send_json(500, {"success": False, "request_id": request_id, "error": "Task store not initialized"})
                return
            task = TASK_STORE.get(task_id)
            if not task:
                self._send_json(404, {"success": False, "request_id": request_id, "error": f"Task not found: {task_id}"})
                return

            from bridge_cancel import cancel
            cancel(task_id)

            # If still pending, mark immediately; if running, mark as cancel_requested (best-effort)
            if task.status in ("PENDING",):
                TASK_STORE.update(
                    task_id,
                    status="CANCELLED",
                    finished_at=time.time(),
                    error={"type": "Cancelled", "message": "Task cancelled by user"},
                )
            elif task.status == "RUNNING":
                # cannot preempt ComfyUI safely without external control, but we can record user's intent
                TASK_STORE.update(
                    task_id,
                    error={"type": "CancelRequested", "message": "Cancellation requested while running; best-effort"},
                )

            LOGGER.info({"event": "cancel", "request_id": request_id, "task_id": task_id, "prev_status": task.status})
            self._send_json(
                200,
                {
                    "success": True,
                    "request_id": request_id,
                    "task_id": task_id,
                    "status": TASK_STORE.get(task_id).status,
                    "note": "If task is already RUNNING, cancellation is best-effort (will apply before execution only).",
                },
            )
            return

        # Async submit endpoint
        if path == "/submit":
            from bridge_metrics import inc
            inc("submit_total")
            request_id = self.headers.get("X-Request-Id") or str(uuid.uuid4())
            try:
                if not TASK_STORE or not TASK_QUEUE:
                    self._send_json(500, {"success": False, "error": "Async system not initialized"})
                    return

                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length).decode("utf-8-sig") if content_length > 0 else "{}"
                request_data = parse_request_body(raw_body)

                # normalize early for reproducibility
                normalized = normalize_request(request_data)

                # apply policy defaults only when user did not provide values
                if POLICY:
                    try:
                        if normalized.get("task_timeout_seconds") is None:
                            normalized["task_timeout_seconds"] = int(POLICY.task_timeout_seconds)
                        else:
                            normalized["task_timeout_seconds"] = int(normalized["task_timeout_seconds"])

                        if normalized.get("max_retries") is None:
                            normalized["max_retries"] = int(POLICY.max_retries)
                        else:
                            normalized["max_retries"] = int(normalized["max_retries"])

                        if normalized.get("retry_backoff_seconds") is None:
                            normalized["retry_backoff_seconds"] = float(POLICY.retry_backoff_seconds)
                        else:
                            normalized["retry_backoff_seconds"] = float(normalized["retry_backoff_seconds"])
                    except Exception:
                        pass

                # workflow routing by profile
                profile = request_data.get("profile") if isinstance(request_data, dict) else None
                try:
                    wf_path = ROUTER.resolve(profile=profile, workflow_override=normalized.get("workflow")) if ROUTER else Path(normalized.get("workflow"))
                    normalized["workflow"] = str(wf_path)
                except Exception as e:
                    raise RuntimeError(f"Workflow routing failed: {e}")

                # P2 reproducibility audit metadata.
                normalized["audit"] = build_audit_fields(normalized)

                task = TASK_STORE.new_task(request_id=request_id, normalized_request=normalized)
                TASK_QUEUE.put(task.task_id)

                LOGGER.info({"event": "submit", "request_id": request_id, "task_id": task.task_id, "profile": profile})

                self._send_json(
                    202,
                    {
                        "success": True,
                        "request_id": request_id,
                        "task_id": task.task_id,
                        "status": task.status,
                        "poll": f"/task/{task.task_id}",
                    },
                )
            except Exception as exc:
                LOGGER.error({"event": "submit_error", "request_id": request_id, "err": str(exc)})
                self._send_json(500, {"success": False, "request_id": request_id, "error": str(exc), "traceback": traceback.format_exc()})
            return

        self._send_json(404, {"success": False, "error": f"Unknown POST path: {path}"})

    def log_message(self, format, *args):
        sys.stdout.write("[BRIDGE] " + (format % args) + "\n")


def check_comfyui(comfyui_server: str):
    stats_url = comfyui_server.rstrip("/") + "/system_stats"
    stats = http_json("GET", stats_url, timeout=15)
    version = stats.get("system", {}).get("comfyui_version", "unknown")
    return version


def main():
    global CONFIG, TASK_STORE, TASK_QUEUE, WORKERS, AUTH, RATE_LIMITER, ROUTER, POLICY

    parser = argparse.ArgumentParser(description="Start a local HTTP bridge for ComfyUI image generation.")

    parser.add_argument("--host", default="127.0.0.1", help="Bridge host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=7861, help="Bridge port. Default: 7861")
    parser.add_argument("--comfyui-server", default=DEFAULT_COMFYUI_SERVER, help="ComfyUI server URL.")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW, help="Default ComfyUI API workflow JSON path.")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Default output folder.")
    parser.add_argument("--timeout", type=int, default=900, help="Generation timeout seconds.")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Polling interval seconds.")
    parser.add_argument("--data-dir", default="bridge_data", help="Bridge data directory (task logs, debug).")
    parser.add_argument("--workers", type=int, default=1, help="Number of background worker threads for /submit.")
    parser.add_argument("--max-queue", type=int, default=64, help="Max queued tasks.")
    parser.add_argument("--config", default="bridge_config.json", help="Bridge config JSON (api_key, rate limit, workflow profiles).")
    parser.add_argument("--api-key", default="", help="Optional API key (overrides config). If set, clients must send X-API-Key.")
    parser.add_argument("--rl-capacity", type=float, default=10.0, help="Rate limit bucket capacity per API key.")
    parser.add_argument("--rl-refill", type=float, default=1.0, help="Rate limit refill tokens per second per API key.")

    args = parser.parse_args()

    # Load config file if present
    config_path = Path(args.config)
    config_obj = {}
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                config_obj = json.load(f) or {}
        except Exception as e:
            print(f"[WARN] Failed to read config {config_path}: {e}")

    api_key = args.api_key or (config_obj.get("api_key") or "")
    AUTH = ApiKeyAuth(api_key if api_key.strip() else None)

    rl = config_obj.get("rate_limit") or {}
    RATE_LIMITER = RateLimiter(
        capacity=float(rl.get("capacity", args.rl_capacity)),
        refill_rate_per_sec=float(rl.get("refill_rate_per_sec", args.rl_refill)),
    )

    profiles = config_obj.get("workflow_profiles") or {"default": DEFAULT_WORKFLOW}
    ROUTER = WorkflowRouter(base_dir=Path(__file__).resolve().parent, profile_map=dict(profiles), default_profile="default")

    POLICY = load_policy(config_obj)

    CONFIG = BridgeConfig(
        comfyui_server=args.comfyui_server,
        workflow_path=args.workflow,
        out_dir=args.out_dir,
        timeout_seconds=args.timeout,
        poll_interval=args.poll_interval,
    )

    print("[INFO] Local Image Bridge starting...")
    print("[INFO] Bridge:", f"http://{args.host}:{args.port}")
    print("[INFO] ComfyUI:", CONFIG.comfyui_server)
    print("[INFO] Workflow:", CONFIG.workflow_path)
    print("[INFO] Output dir:", CONFIG.out_dir)

    if not CONFIG.workflow_path.exists():
        print(f"[FAILED] Workflow file not found: {CONFIG.workflow_path}")
        sys.exit(1)

    try:
        comfyui_version = check_comfyui(CONFIG.comfyui_server)
        print("[OK] ComfyUI API reachable.")
        print("[INFO] ComfyUI version:", comfyui_version)
    except Exception as exc:
        print("[FAILED] Cannot reach ComfyUI API.")
        print(str(exc))
        sys.exit(1)

    # Init async system
    data_dir = Path(args.data_dir)
    TASK_STORE = TaskStore(data_dir)
    TASK_QUEUE = queue.Queue(maxsize=int(args.max_queue))

    # Start worker threads
    # NOTE: we run a worker-side job function that does not depend on bridge globals,
    # because we may enforce hard timeouts in a separate process.
    from bridge_job import run_generation

    WORKERS.clear()
    worker_count = max(1, int(args.workers))
    for i in range(worker_count):
        w = Worker(
            name=f"bridge_worker_{i+1}",
            q=TASK_QUEUE,
            store=TASK_STORE,
            run_task_fn=run_generation,
            logger=LOGGER,
        )
        w.start()
        WORKERS.append(w)

    server = ThreadingHTTPServer((args.host, args.port), ImageBridgeHandler)

    print("[OK] Bridge is ready.")
    print("[INFO] Health check: GET  /health")
    print("[INFO] Generate:     POST /generate-image   (sync)")
    print("[INFO] Submit:       POST /submit          (async)")
    print("[INFO] Task status:  GET  /task/{task_id}")
    print("[INFO] Metrics:      GET  /metrics")
    print("[INFO] Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Bridge stopped by user.")
    finally:
        try:
            for w in WORKERS:
                w.stop()
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()
