"""Download quantized Gemma 4 E4B IT and run a local Ollama smoke test.

Start Ollama first (open the Ollama app, or run `ollama serve`).
    python tests/test_ollama.py
    python tests/test_ollama.py --pull-only
    python tests/test_ollama.py --skip-pull --image page_1.png
    python tests/test_ollama.py --prompt "Translate to Vietnamese: Thank you!"

Uses only the Python standard library. Default model download is about 6.6 GB.
Model tags: https://ollama.com/library/gemma4/tags
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "gemma4:e4b-it-q4_K_M"


def request_json(host: str, endpoint: str, payload: dict, timeout: float):
    request = Request(
        f"{host.rstrip('/')}/api/{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urlopen(request, timeout=timeout)


def pull_model(host: str, model: str, timeout: float) -> None:
    print(f"Downloading {model} (cached layers will be reused)...", flush=True)
    success = False
    with request_json(host, "pull", {"model": model, "stream": True}, timeout) as response:
        for line in response:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("error"):
                raise RuntimeError(event["error"])
            status = event.get("status", "")
            total = event.get("total", 0)
            completed = event.get("completed", 0)
            progress = f" {completed / total:.1%}" if total else ""
            print(f"\r{status}{progress}".ljust(100), end="", flush=True)
            success = status == "success"
    print()
    if not success:
        raise RuntimeError("Model download ended without a success response; rerun to resume.")


def run_chat(args: argparse.Namespace) -> None:
    message = {"role": "user", "content": args.prompt}
    if args.image:
        message["images"] = [base64.b64encode(args.image.read_bytes()).decode("ascii")]
    payload = {
        "model": args.model,
        "messages": [message],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
        },
    }
    print(f"Running {args.model}...", flush=True)
    with request_json(args.host, "chat", payload, args.timeout) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError(result["error"])
    content = (result.get("message") or {}).get("content")
    if not content or not content.strip():
        raise RuntimeError("Ollama returned an empty answer.")
    print(f"\n{content}\n")
    duration = result.get("total_duration", 0) / 1e9
    tokens = result.get("eval_count", 0)
    eval_seconds = result.get("eval_duration", 0) / 1e9
    speed = tokens / eval_seconds if eval_seconds else 0
    print(f"Total: {duration:.2f}s | Output: {tokens} tokens | Speed: {speed:.2f} tokens/s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama server URL")
    parser.add_argument("--prompt", default="Translate this manga dialogue into natural Vietnamese. Return only the translation: I will protect everyone, no matter what!")
    parser.add_argument("--image", type=Path, help="Optional manga image; supply an image-specific --prompt")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pull-only", action="store_true", help="Download without running inference")
    mode.add_argument("--skip-pull", action="store_true", help="Run an already downloaded model")
    parser.add_argument("--timeout", type=float, default=1800, help="Socket timeout in seconds (default: 1800)")
    parser.add_argument("--num-ctx", type=int, default=4096, help="Context size (default: 4096)")
    parser.add_argument("--num-predict", type=int, default=512, help="Maximum output tokens (default: 512)")
    args = parser.parse_args()
    if args.timeout <= 0 or args.num_ctx <= 0 or args.num_predict <= 0:
        parser.error("--timeout, --num-ctx and --num-predict must be positive")
    if args.image and not args.image.is_file():
        parser.error(f"Image does not exist: {args.image}")
    return args


def main() -> int:
    args = parse_args()
    try:
        if not args.skip_pull:
            pull_model(args.host, args.model, args.timeout)
        if not args.pull_only:
            run_chat(args)
        return 0
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Ollama HTTP {exc.code}: {detail}", file=sys.stderr)
    except URLError as exc:
        print(f"Cannot connect to Ollama at {args.host}: {exc.reason}. Open Ollama or run `ollama serve` first.", file=sys.stderr)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Ollama test failed: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nCancelled. Rerun the script to resume downloading cached layers.", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
