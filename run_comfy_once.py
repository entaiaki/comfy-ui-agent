#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_comfy_once.py

Minimal ComfyUI API smoke test script.

What it does:
1. Reads a ComfyUI API workflow JSON file.
2. Replaces prompt / negative prompt / seed / size / steps if requested.
3. POSTs the workflow to ComfyUI /prompt.
4. Polls /history/{prompt_id}.
5. Downloads generated images from /view.
6. Saves images to a local output folder.

Default ComfyUI server:
http://127.0.0.1:8186

Default workflow:
flux_kontext_txt2img_api_workflow.json
"""

import argparse
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_PROMPT = (
    "A white metal storage shelf as the main subject, clean and sturdy industrial structure "
    "with precise geometric lines, displayed in a bright minimal environment, pristine neutral "
    "background, soft diffused studio lighting from above and slightly to the side, natural "
    "realistic shadows beneath the shelf, sharp clear product edges, accurate proportions and "
    "scale, high-end Taobao e-commerce product photography style, clean composition with "
    "generous empty space for Chinese title text overlay, premium material texture visible on "
    "the metal surface, crisp fine details, professional commercial advertising quality, "
    "visually trustworthy and practical presentation."
)

DEFAULT_NEGATIVE = (
    "cluttered background, warped shelf structure, wrong product proportions, messy random items, "
    "harsh dark lighting, low-resolution texture, watermark, random text, unrealistic perspective, "
    "overexposure, distracting elements"
)


def read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def http_json(method: str, url: str, payload=None, timeout: int = 30) -> dict:
    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
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


def http_bytes(url: str, timeout: int = 60) -> bytes:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} error from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot download from {url}\nReason: {e}") from e


def set_if_exists(workflow: dict, node_id: str, input_name: str, value):
    node = workflow.get(str(node_id))
    if not node:
        print(f"[WARN] Node {node_id} not found, skipped setting {input_name}.")
        return
    inputs = node.setdefault("inputs", {})
    if input_name not in inputs:
        print(f"[WARN] Node {node_id} has no input '{input_name}', creating it anyway.")
    inputs[input_name] = value


def patch_workflow(
    workflow: dict,
    prompt: str,
    negative: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    filename_prefix: str,
):
    """
    This patching logic matches the workflow we created earlier:

    Node 5  = positive prompt
    Node 7  = negative prompt
    Node 9  = KSampler seed / steps
    Node 4  = EmptySD3LatentImage width / height
    Node 8  = ModelSamplingFlux width / height
    Node 11 = SaveImage filename prefix
    """

    set_if_exists(workflow, "5", "text", prompt)
    set_if_exists(workflow, "7", "text", negative)

    set_if_exists(workflow, "9", "seed", seed)
    set_if_exists(workflow, "9", "steps", steps)

    set_if_exists(workflow, "4", "width", width)
    set_if_exists(workflow, "4", "height", height)

    set_if_exists(workflow, "8", "width", width)
    set_if_exists(workflow, "8", "height", height)

    set_if_exists(workflow, "11", "filename_prefix", filename_prefix)

    return workflow


def submit_prompt(server: str, workflow: dict, timeout: int = 30) -> str:
    url = server.rstrip("/") + "/prompt"
    client_id = str(uuid.uuid4())

    payload = {
        "prompt": workflow,
        "client_id": client_id,
    }

    result = http_json("POST", url, payload=payload, timeout=timeout)

    if "node_errors" in result and result["node_errors"]:
        raise RuntimeError(
            "ComfyUI rejected the workflow with node_errors:\n"
            + json.dumps(result["node_errors"], ensure_ascii=False, indent=2)
        )

    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI did not return prompt_id:\n" + json.dumps(result, ensure_ascii=False, indent=2))

    return prompt_id


def wait_for_history(server: str, prompt_id: str, timeout_seconds: int = 900, interval: float = 2.0) -> dict:
    url = server.rstrip("/") + f"/history/{prompt_id}"
    start = time.time()

    while True:
        result = http_json("GET", url, timeout=30)

        if prompt_id in result:
            history = result[prompt_id]

            # If execution finished, outputs should appear here.
            outputs = history.get("outputs", {})
            if outputs:
                return history

            # Some failed executions may still appear without outputs.
            status = history.get("status", {})
            if status.get("completed") is True:
                return history

        if time.time() - start > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for prompt_id={prompt_id}")

        print(f"[WAIT] prompt_id={prompt_id} still running...")
        time.sleep(interval)


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


def safe_filename(name: str) -> str:
    bad_chars = '<>:"/\\|?*'
    for ch in bad_chars:
        name = name.replace(ch, "_")
    return name


def download_images(server: str, images: list, out_dir: Path) -> list:
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
        url = server.rstrip("/") + "/view?" + query

        data = http_bytes(url, timeout=120)

        filename = safe_filename(image["filename"] or f"comfyui_{int(time.time())}.png")
        save_path = out_dir / filename
        save_path.write_bytes(data)

        saved_paths.append(save_path)

    return saved_paths


def main():
    parser = argparse.ArgumentParser(description="Run one ComfyUI workflow through the HTTP API.")

    parser.add_argument(
        "--server",
        default="http://127.0.0.1:8186",
        help="ComfyUI server URL. Default: http://127.0.0.1:8186",
    )
    parser.add_argument(
        "--workflow",
        default="flux_kontext_txt2img_api_workflow.json",
        help="Path to ComfyUI API workflow JSON.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Positive prompt.",
    )
    parser.add_argument(
        "--negative",
        default=DEFAULT_NEGATIVE,
        help="Negative prompt.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=-1,
        help="Seed. Use -1 for random.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1024,
        help="Image width.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1280,
        help="Image height.",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=24,
        help="Sampling steps.",
    )
    parser.add_argument(
        "--filename-prefix",
        default="Flux_Kontext_API_Test",
        help="ComfyUI SaveImage filename prefix.",
    )
    parser.add_argument(
        "--out-dir",
        default="generated_outputs",
        help="Local folder to save downloaded images.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Max seconds to wait for generation.",
    )

    args = parser.parse_args()

    workflow_path = Path(args.workflow)
    out_dir = Path(args.out_dir)

    seed = args.seed if args.seed >= 0 else random.randint(0, 2**63 - 1)

    print("[INFO] Server:", args.server)
    print("[INFO] Workflow:", workflow_path)
    print("[INFO] Width x Height:", args.width, "x", args.height)
    print("[INFO] Steps:", args.steps)
    print("[INFO] Seed:", seed)

    # 1. Basic server check.
    stats_url = args.server.rstrip("/") + "/system_stats"
    stats = http_json("GET", stats_url, timeout=15)
    print("[OK] ComfyUI API is reachable.")
    print("[INFO] ComfyUI version:", stats.get("system", {}).get("comfyui_version", "unknown"))

    # 2. Read and patch workflow.
    workflow = read_json(workflow_path)
    workflow = patch_workflow(
        workflow=workflow,
        prompt=args.prompt,
        negative=args.negative,
        seed=seed,
        width=args.width,
        height=args.height,
        steps=args.steps,
        filename_prefix=args.filename_prefix,
    )

    # Optional debug copy.
    debug_workflow_path = out_dir / "last_submitted_workflow.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    debug_workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3. Submit.
    prompt_id = submit_prompt(args.server, workflow)
    print("[OK] Submitted workflow.")
    print("[INFO] prompt_id:", prompt_id)

    # 4. Wait.
    history = wait_for_history(args.server, prompt_id, timeout_seconds=args.timeout)
    status = history.get("status", {})
    print("[INFO] Status:", json.dumps(status, ensure_ascii=False))

    # 5. Extract and download images.
    images = extract_images_from_history(history)
    if not images:
        print("[ERROR] No output images found in history.")
        print(json.dumps(history, ensure_ascii=False, indent=2))
        sys.exit(2)

    saved_paths = download_images(args.server, images, out_dir)

    print("[OK] Images saved:")
    for path in saved_paths:
        print(" -", path.resolve())

    print("[DONE] ComfyUI API smoke test completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("[FAILED]", str(exc))
        sys.exit(1)
