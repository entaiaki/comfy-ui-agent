#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_qwenpaw_json.py

Read QwenPaw output from clipboard and send it to local_image_bridge_v03.py.

Default bridge endpoint:
http://127.0.0.1:7861/generate-image

Typical workflow:
1. Start bridge:
   python local_image_bridge_v03.py

2. Copy QwenPaw's whole JSON output or markdown answer.

3. Run:
   python send_qwenpaw_json.py

The script will:
- read clipboard text
- send it to the bridge
- print returned prompt_id and image paths
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_ENDPOINT = "http://127.0.0.1:7861/generate-image"
DEFAULT_HEALTH = "http://127.0.0.1:7861/health"


def read_clipboard_windows() -> str:
    """
    Read clipboard text using PowerShell.
    This avoids requiring pyperclip or other third-party packages.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return result.stdout.strip()
    except Exception as exc:
        raise RuntimeError(f"Failed to read clipboard with PowerShell: {exc}") from exc


def read_text_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8-sig")


def http_get_json(url: str, timeout: int = 15) -> dict:
    try:
        with urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot connect to {url}\nReason: {e}") from e


def http_post_raw(url: str, body: str, timeout: int = 1200) -> dict:
    data = body.encode("utf-8")
    req = Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
        },
    )

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}\n{body}") from e
    except URLError as e:
        raise RuntimeError(f"Cannot connect to {url}\nReason: {e}") from e


def print_result(result: dict):
    success = result.get("success", False)

    print("\n========== Bridge Result ==========")
    print("success:", success)

    if not success:
        print("error:", result.get("error", "Unknown error"))
        if "traceback" in result:
            print("\ntraceback:")
            print(result["traceback"])
        return

    print("prompt_id:", result.get("prompt_id"))
    print("seed:", result.get("seed"))
    print("size:", f'{result.get("width")} x {result.get("height")}')
    print("steps:", result.get("steps"))
    print("filename_prefix:", result.get("filename_prefix"))

    image_paths = result.get("image_paths", [])
    print("\nimage_paths:")
    for path in image_paths:
        print(" -", path)

    normalized = result.get("normalized_request")
    if normalized:
        print("\nnormalized_request:")
        print(json.dumps(normalized, ensure_ascii=False, indent=2))

    print("===================================\n")


def main():
    parser = argparse.ArgumentParser(
        description="Send QwenPaw JSON or markdown-wrapped JSON to local_image_bridge_v03.py."
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Bridge generation endpoint. Default: {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--health",
        default=DEFAULT_HEALTH,
        help=f"Bridge health endpoint. Default: {DEFAULT_HEALTH}",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Optional text/json file to send instead of clipboard.",
    )
    parser.add_argument(
        "--no-health-check",
        action="store_true",
        help="Skip bridge health check.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help="HTTP timeout in seconds. Default: 1200.",
    )
    args = parser.parse_args()

    print("[INFO] Target endpoint:", args.endpoint)

    if not args.no_health_check:
        print("[INFO] Checking bridge health...")
        health = http_get_json(args.health, timeout=15)
        print("[OK] Bridge health:", health.get("message", "ok"))
        print("[INFO] Bridge version:", health.get("version", "unknown"))

    if args.file:
        body = read_text_file(Path(args.file))
        print("[INFO] Loaded request body from file:", args.file)
    else:
        body = read_clipboard_windows()
        print("[INFO] Loaded request body from clipboard.")

    if not body.strip():
        raise RuntimeError("Clipboard/file is empty. Copy QwenPaw output first.")

    print("[INFO] Sending request to bridge...")
    result = http_post_raw(args.endpoint, body, timeout=args.timeout)

    print_result(result)

    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\n[FAILED]", str(exc))
        sys.exit(1)
