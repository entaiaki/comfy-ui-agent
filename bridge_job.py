#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_job.py

Worker-side job runner.

Important: The bridge worker may execute in a separate process when enforcing timeouts.
Therefore this module must NOT depend on globals from local_image_bridge_v03.

It receives a fully-normalized request dict that must include:
- comfyui_server
- workflow (path)
- out_dir

It will call ComfyUI API endpoints directly.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def _http_json(method: str, url: str, payload=None, timeout: int = 30) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} error from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot connect to {url}\nReason: {e}") from e


def _http_bytes(url: str, timeout: int = 120) -> bytes:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} error from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot download from {url}\nReason: {e}") from e


def _read_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _patch_workflow(workflow: dict, prompt: str, negative: str, seed: int, width: int, height: int, steps: int, filename_prefix: str) -> dict:
    # We keep the same patch strategy as local_image_bridge_v03: node ids are workflow-dependent.
    # This expects the workflow json already matches your Flux Kontext API template.

    def set_if_exists(node_id: str, input_name: str, value):
        node = workflow.get(str(node_id))
        if not node:
            return
        inputs = node.setdefault("inputs", {})
        inputs[input_name] = value

    # Common node ids used in your workflow template
    set_if_exists("6", "text", prompt)
    set_if_exists("7", "text", negative)
    set_if_exists("3", "seed", seed)
    set_if_exists("5", "width", width)
    set_if_exists("5", "height", height)
    set_if_exists("3", "steps", steps)
    set_if_exists("9", "filename_prefix", filename_prefix)

    return workflow


def _submit_prompt(comfyui_server: str, workflow: dict) -> str:
    url = comfyui_server.rstrip("/") + "/prompt"
    payload = {"prompt": workflow, "client_id": "bridge"}
    resp = _http_json("POST", url, payload=payload, timeout=60)
    prompt_id = resp.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt did not return prompt_id: {resp}")
    return prompt_id


def _wait_for_history(comfyui_server: str, prompt_id: str, timeout_seconds: int = 900, poll_interval: float = 2.0) -> dict:
    deadline = time.time() + int(timeout_seconds)
    url = comfyui_server.rstrip("/") + f"/history/{prompt_id}"

    while time.time() < deadline:
        history = _http_json("GET", url, timeout=60)
        info = history.get(prompt_id)
        if info and info.get("status") and info.get("status", {}).get("completed") is True:
            return history
        time.sleep(float(poll_interval))

    raise TimeoutError(f"ComfyUI history timeout for prompt_id={prompt_id}")


def _extract_images(history: dict, prompt_id: str) -> list[dict]:
    info = history.get(prompt_id) or {}
    outputs = info.get("outputs") or {}
    images: list[dict] = []
    for node_id, out in outputs.items():
        for img in out.get("images", []) or []:
            images.append(img)
    return images


def _download_images(comfyui_server: str, images: list[dict], out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for img in images:
        filename = img.get("filename")
        subfolder = img.get("subfolder", "")
        ftype = img.get("type", "output")
        if not filename:
            continue
        q = urlencode({"filename": filename, "subfolder": subfolder, "type": ftype})
        url = comfyui_server.rstrip("/") + "/view?" + q
        data = _http_bytes(url, timeout=120)
        out_path = out_dir / filename
        out_path.write_bytes(data)
        saved.append(str(out_path.resolve()))
    return saved


def run_generation(req: dict) -> dict:
    comfyui_server = str(req.get("comfyui_server") or "").rstrip("/")
    workflow_path = Path(str(req.get("workflow") or ""))
    out_dir = Path(str(req.get("out_dir") or "generated_outputs"))

    if not comfyui_server:
        raise RuntimeError("Missing comfyui_server")
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")

    workflow = _read_json_file(workflow_path)
    workflow = _patch_workflow(
        workflow=workflow,
        prompt=str(req.get("prompt") or ""),
        negative=str(req.get("negative") or ""),
        seed=int(req.get("seed") or 0),
        width=int(req.get("width") or 1024),
        height=int(req.get("height") or 1280),
        steps=int(req.get("steps") or 24),
        filename_prefix=str(req.get("filename_prefix") or "Flux_Bridge"),
    )

    prompt_id = _submit_prompt(comfyui_server, workflow)
    history = _wait_for_history(comfyui_server, prompt_id, timeout_seconds=int(req.get("task_timeout_seconds") or 900), poll_interval=2.0)
    images = _extract_images(history, prompt_id)
    if not images:
        raise RuntimeError("Generation completed but no images found")

    image_paths = _download_images(comfyui_server, images, out_dir)

    return {
        "success": True,
        "prompt_id": prompt_id,
        "seed": int(req.get("seed") or 0),
        "width": int(req.get("width") or 0),
        "height": int(req.get("height") or 0),
        "steps": int(req.get("steps") or 0),
        "filename_prefix": str(req.get("filename_prefix") or ""),
        "image_paths": image_paths,
    }
