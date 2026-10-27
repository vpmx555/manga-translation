"""PC commands for atomic code sync and controlling the isolated Linux runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import tempfile
import uuid
import zipfile

PROJECT = Path(__file__).resolve().parents[2]
STATE = PROJECT / ".runpod"
CONNECTION = STATE / "connection.json"
SKIP = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".runpod"}
EXTENSIONS = {".py", ".json", ".md", ".txt", ".in", ".ps1", ".ttf", ".html", ".css", ".js"}
CODE_ROOTS = ["src", "configs", "assets/fonts", "deployment/runpod", "scripts", "tests", "docs/news", "docs/v6", "docs/v7",
              "docs/archive/news", "docs/archive/v6"]
CODE_FILES = ["run.py", "README.md", "docs/README.md", "pytest.ini", "requirements-names.txt", "requirements-visual-layout.txt",
              "requirements-hayai.txt", "requirements-glm-ocr.txt", "runpod.ps1", "runpod-v6.ps1", "AGENTS.md",
              "docs/plans/runpod-v6-agent-operational-plan.md",
              "docs/plans/qwen35-vllm-4090-implementation-vi.md",
              "docs/plans/iris-zero-ch1-vllm-5090-secure-resume-20261016.md",
              "docs/archive/plans/runpod-v6-agent-operational-plan.md",
              "docs/archive/plans/qwen35-vllm-4090-implementation-vi.md",
              "docs/archive/plans/iris-zero-ch1-vllm-5090-secure-resume-20261016.md",
              "docs/archive/plans/identity-bootstrap-v7-design.md"]


def load_connection():
    if not CONNECTION.exists():
        raise RuntimeError("Missing .runpod/connection.json; configure the authorized native connection")
    config = json.loads(CONNECTION.read_text(encoding="utf-8"))
    if not isinstance(config.get("port"), int) or not 1 <= config["port"] <= 65535:
        raise RuntimeError("Invalid SSH TCP port")
    if not Path(config["key"]).is_file():
        raise RuntimeError("SSH private key not found")
    host = config.get("host", "")
    user = config.get("user", "root")
    if not host or host.startswith("-") or any(c in host for c in "\r\n \t/@"):
        raise RuntimeError("Invalid SSH host")
    if not user or user.startswith("-") or any(c in user for c in "\r\n \t/@"):
        raise RuntimeError("Invalid SSH user")
    return config


def ssh_executable(name):
    if os.name == "nt":
        native = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/OpenSSH" / (name + ".exe")
        if native.is_file():
            return str(native)
    return name


def ssh_path(path):
    from pathlib import PureWindowsPath
    text = str(path).replace("\\", "/")
    if os.name == "nt" and ssh_executable("ssh") == "ssh":
        drive = PureWindowsPath(text).drive
        if len(drive) == 2 and drive[1] == ":":
            return "/" + drive[0].lower() + text[2:]
    return text


def ssh_options(config, *, scp=False):
    STATE.mkdir(parents=True, exist_ok=True)
    return ["-i", ssh_path(config["key"]), "-P" if scp else "-p", str(config["port"]),
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", 'UserKnownHostsFile="' + ssh_path(STATE / "known_hosts") + '"']


def execute(argv, *, capture=False):
    result = subprocess.run([str(a) for a in argv], cwd=PROJECT, text=True,
                            capture_output=capture)
    if result.returncode:
        if capture:
            print((result.stderr or result.stdout)[-4000:], flush=True)
        raise RuntimeError(f"Command failed ({result.returncode}): {argv[0]}")
    return result.stdout if capture else result.returncode


def remote(config, arguments, *, capture=False):
    command = shlex.join([str(a) for a in arguments])
    return execute([ssh_executable("ssh"), *ssh_options(config), config.get("user", "root") + "@" + config["host"],
                    command], capture=capture)


def upload(config, local, destination):
    if not destination.startswith("/workspace/") or ".." in PurePosixPath(destination).parts:
        raise RuntimeError("Uploads must stay inside /workspace")
    target = config.get("user", "root") + "@" + config["host"] + ":" + destination
    execute([ssh_executable("scp"), *ssh_options(config, scp=True), ssh_path(local), target])



def code_files(root=PROJECT):
    files = []
    for relative in CODE_FILES:
        path = root / relative
        if path.is_file():
            files.append(path)
    for relative in CODE_ROOTS:
        base = root / relative
        if not base.exists():
            continue
        for directory, subdirectories, names in os.walk(base, followlinks=False):
            subdirectories[:] = [name for name in subdirectories if name not in SKIP and not name.startswith(".")]
            for name in names:
                path = Path(directory) / name
                if path.suffix in EXTENSIONS and not name.startswith(".") and not path.is_symlink():
                    path.resolve().relative_to(root.resolve())
                    files.append(path)
    return sorted(set(files))


def bundle(destination, root=PROJECT):
    manifest = {"files": {}, "format": 1}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in code_files(root):
            name = path.relative_to(root).as_posix()
            content = path.read_bytes()
            if name.endswith(".py"):
                compile(content, name, "exec")
            archive.writestr(name, content)
            manifest["files"][name] = hashlib.sha256(content).hexdigest()
        manifest["version"] = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()
        archive.writestr("__bundle__.json", json.dumps(manifest, ensure_ascii=False))
    return manifest

