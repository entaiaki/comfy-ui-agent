#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qwenpaw_flux_image_mcp.py

MCP tool server for QwenPaw → ComfyUI Flux Kontext image generation.

Goal:
- QwenPaw calls this MCP tool directly.
- No manual JSON copy.
- No send_qwenpaw_json.py needed during normal use.
- ComfyUI must still be running at http://127.0.0.1:8186.

Default files expected in the same folder:
- flux_kontext_txt2img_api_workflow.json

Exposed MCP tools:
- generate_flux_image
- flux_image_health_check
"""

import json
import random
import re
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    # when executed from this folder
    from bridge_client import submit_and_wait
except ModuleNotFoundError:
    # when imported as a package
    from gpt_image_agent.bridge_client import submit_and_wait


try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:
    raise RuntimeError(
        "Missing dependency: mcp\n"
        "Install it with:\n"
        "python -m pip install mcp\n"
        f"Original error: {exc}"
    )


APP_DIR = Path(__file__).resolve().parent

DEFAULT_COMFYUI_SERVER = "http://127.0.0.1:8186"
DEFAULT_WORKFLOW = str(APP_DIR / "flux_kontext_txt2img_api_workflow.json")
DEFAULT_OUT_DIR = str(APP_DIR / "generated_outputs")

# Prefer routing through the local bridge (async task model)
DEFAULT_BRIDGE_SERVER = "http://127.0.0.1:7861"

DEFAULT_NEGATIVE = (
    "watermark, text, logo, low-resolution texture, blurry image, distorted anatomy, "
    "malformed hands, malformed face, distorted architecture, unrealistic perspective, "
    "random objects, chaotic composition, overexposure, underexposure"
)

mcp = FastMCP("local_flux_image")


def _read_json_file(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


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


def _sanitize_filename_prefix(prefix: str) -> str:
    prefix = str(prefix or "Flux_Image").strip()
    prefix = prefix.replace("-", "_").replace(" ", "_")
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", prefix)
    prefix = re.sub(r"_+", "_", prefix).strip("_")
    return (prefix or "Flux_Image")[:80]


def _set_if_exists(workflow: dict, node_id: str, input_name: str, value):
    node = workflow.get(str(node_id))
    if not node:
        return
    node.setdefault("inputs", {})[input_name] = value


def _patch_workflow(
    workflow: dict,
    prompt: str,
    negative: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    filename_prefix: str,
) -> dict:
    """
    Matches current tested Flux Kontext TXT2IMG API workflow:

    Node 5  = positive prompt
    Node 7  = negative prompt
    Node 9  = KSampler seed / steps
    Node 4  = EmptySD3LatentImage width / height
    Node 8  = ModelSamplingFlux width / height
    Node 11 = SaveImage filename prefix
    """

    _set_if_exists(workflow, "5", "text", prompt)
    _set_if_exists(workflow, "7", "text", negative)

    _set_if_exists(workflow, "9", "seed", seed)
    _set_if_exists(workflow, "9", "steps", steps)

    _set_if_exists(workflow, "4", "width", width)
    _set_if_exists(workflow, "4", "height", height)

    _set_if_exists(workflow, "8", "width", width)
    _set_if_exists(workflow, "8", "height", height)

    _set_if_exists(workflow, "11", "filename_prefix", filename_prefix)

    return workflow


def _submit_prompt(comfyui_server: str, workflow: dict) -> str:
    payload = {
        "prompt": workflow,
        "client_id": str(uuid.uuid4()),
    }

    result = _http_json("POST", comfyui_server.rstrip("/") + "/prompt", payload=payload, timeout=30)

    if result.get("node_errors"):
        raise RuntimeError(
            "ComfyUI rejected the workflow:\n"
            + json.dumps(result["node_errors"], ensure_ascii=False, indent=2)
        )

    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI did not return prompt_id:\n" + json.dumps(result, ensure_ascii=False, indent=2))

    return prompt_id


def _wait_for_history(
    comfyui_server: str,
    prompt_id: str,
    timeout_seconds: int = 900,
    poll_interval: float = 2.0,
) -> dict:
    url = comfyui_server.rstrip("/") + f"/history/{prompt_id}"
    start = time.time()

    while True:
        result = _http_json("GET", url, timeout=30)

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


def _extract_images(history: dict) -> list[dict]:
    images = []
    for node_id, node_output in history.get("outputs", {}).items():
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


def _download_images(comfyui_server: str, images: list[dict], out_dir: Path) -> list[str]:
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
        data = _http_bytes(url, timeout=120)

        filename = image["filename"] or f"comfyui_{int(time.time())}.png"
        filename = re.sub(r'[<>:"/\\\\|?*]', "_", filename)

        save_path = out_dir / filename
        save_path.write_bytes(data)
        saved_paths.append(str(save_path.resolve()))

    return saved_paths


@mcp.tool()
def flux_image_health_check(
    comfyui_server: str = DEFAULT_COMFYUI_SERVER,
    workflow_path: str = DEFAULT_WORKFLOW,
) -> dict:
    """
    Check whether the local Flux image generation environment is ready.

    Use this tool before image generation if you need to verify ComfyUI and workflow availability.
    """
    workflow_file = Path(workflow_path)

    stats = _http_json("GET", comfyui_server.rstrip("/") + "/system_stats", timeout=15)

    return {
        "success": True,
        "comfyui_server": comfyui_server,
        "comfyui_version": stats.get("system", {}).get("comfyui_version", "unknown"),
        "workflow_path": str(workflow_file.resolve()),
        "workflow_exists": workflow_file.exists(),
        "output_dir": DEFAULT_OUT_DIR,
    }


@mcp.tool()
def generate_flux_image(
    prompt: str,
    negative: str = DEFAULT_NEGATIVE,
    width: int = 1024,
    height: int = 1280,
    steps: int = 24,
    seed: int = -1,
    filename_prefix: str = "Flux_Image",
    comfyui_server: str = DEFAULT_COMFYUI_SERVER,
    workflow_path: str = DEFAULT_WORKFLOW,
    out_dir: str = DEFAULT_OUT_DIR,
    bridge_server: str = DEFAULT_BRIDGE_SERVER,
    bridge_api_key: str = "",
    profile: str = "default",
) -> dict:
    """
    Generate an image with local ComfyUI Flux Kontext.

    The input prompt should be a polished English Flux prompt.
    Use this tool when the user asks to generate, create, draw, render, or design an image.

    Args:
        prompt: Final polished English positive prompt.
        negative: Negative prompt.
        width: Image width. Use 1280 for horizontal promotional images.
        height: Image height. Use 768 for horizontal promotional images.
        steps: Sampling steps. Usually 24.
        seed: Use -1 for random seed.
        filename_prefix: English letters, numbers, and underscores only.
        comfyui_server: Local ComfyUI server.
        workflow_path: ComfyUI API workflow JSON.
        out_dir: Output folder.

    Returns:
        JSON with success, prompt_id, seed, size, and image_paths.
    """
    if not prompt or not str(prompt).strip():
        return {
            "success": False,
            "error": "prompt is empty",
        }

    width = int(width)
    height = int(height)
    steps = int(steps)
    seed = int(seed)

    if seed < 0:
        seed = random.randint(0, 2**63 - 1)

    filename_prefix = _sanitize_filename_prefix(filename_prefix)

    # Prefer using the local bridge async model.
    # This makes MCP calls consistent with bridge observability, queueing, and reproducibility.

    payload = {
        "prompt": str(prompt),
        "negative": str(negative or DEFAULT_NEGATIVE),
        "width": width,
        "height": height,
        "steps": steps,
        "seed": seed,
        "filename_prefix": filename_prefix,
        "comfyui_server": comfyui_server,
        "workflow": workflow_path,
        "out_dir": out_dir,
        "profile": profile,
    }

    task_resp = submit_and_wait(
        bridge_server=bridge_server,
        payload=payload,
        api_key=bridge_api_key,
        poll_interval=1.0,
        timeout_seconds=900,
    )

    if not task_resp.get("success"):
        return task_resp

    task = task_resp.get("task") or {}
    if task.get("status") != "SUCCEEDED":
        return {
            "success": False,
            "error": "bridge_task_failed",
            "task": task,
        }

    result = task.get("result") or {}
    # keep compatibility with previous return shape as much as possible
    return result


if __name__ == "__main__":
    mcp.run()
