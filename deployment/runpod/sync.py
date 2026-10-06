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
REMOTE_PROJECT = "/workspace/manga-translate-v2"
REMOTE_RUNTIME = "/workspace/manga-runtime"
REMOTE_MANAGE = REMOTE_PROJECT + "/deployment/runpod/manage.py"
SKIP = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".runpod"}
EXTENSIONS = {".py", ".json", ".md", ".txt", ".in"}
CODE_ROOTS = ["src", "configs", "deployment/runpod", "scripts", "tests", "docs/news"]
CODE_FILES = ["run.py", "README.md", "requirements-names.txt", "runpod.ps1"]


def load_connection():
    if not CONNECTION.exists():
        raise RuntimeError("Missing .runpod/connection.json; run sync.py connect --help")
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


def fetch_data(config, kind, destination):
    destination = destination.resolve()
    if destination.exists():
        raise RuntimeError("Download destination already exists; choose a new backup folder")
    if kind not in {"outputs", "banks"}:
        raise ValueError("Only outputs or banks can be downloaded")
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive = REMOTE_RUNTIME + "/outgoing/" + uuid.uuid4().hex + ".zip"
    script = (
        "import pathlib,sys,zipfile; source=pathlib.Path(sys.argv[1]); "
        "archive=pathlib.Path(sys.argv[2]); archive.parent.mkdir(parents=True,exist_ok=True); "
        "assert source.is_dir(), 'Remote data directory does not exist'; "
        "z=zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1); "
        "[z.write(p,p.relative_to(source).as_posix()) for p in source.rglob('*') "
        "if p.is_file() and not p.is_symlink()]; z.close()"
    )
    try:
        remote(config, ["python3", "-c", script, REMOTE_RUNTIME + "/data/" + kind, archive])
        STATE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fetch-", dir=STATE) as temporary:
            local = Path(temporary) / "data.zip"
            source = config.get("user", "root") + "@" + config["host"] + ":" + archive
            execute([ssh_executable("scp"), *ssh_options(config, scp=True), source, ssh_path(local)])
            with zipfile.ZipFile(local) as zipped:
                for info in zipped.infolist():
                    name = PurePosixPath(info.filename)
                    if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename:
                        raise RuntimeError("Unsafe path in downloaded archive")
                zipped.extractall(destination)
        print(f"Downloaded {kind}: {destination}", flush=True)
    finally:
        remote(config, ["python3", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)", archive])


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


def sync(config):
    with tempfile.TemporaryDirectory(prefix="manga-runpod-") as temp:
        archive = Path(temp) / "code.zip"
        manifest = bundle(archive, PROJECT)
        print(f"Sync {len(manifest['files'])} files; version {manifest['version'][:12]}", flush=True)
        exists = remote(config, ["python3", "-c", "from pathlib import Path; print(int(Path(" +
                                repr(REMOTE_MANAGE) + ").is_file()))"], capture=True).strip()
        if exists == "1":
            current = remote(config, ["python3", REMOTE_MANAGE, "code-status"], capture=True).strip()
            if current == manifest["version"]:
                print("Code unchanged; nothing uploaded or reinstalled", flush=True)
                return
        incoming = REMOTE_RUNTIME + "/incoming/" + uuid.uuid4().hex + ".zip"
        remote(config, ["mkdir", "-p", REMOTE_RUNTIME + "/incoming", REMOTE_PROJECT + "/deployment/runpod"])
        upload(config, archive, incoming)
        # Only bootstrap the controller when none exists; never overwrite live code piecemeal.
        if exists != "1":
            upload(config, PROJECT / "deployment/runpod/manage.py", REMOTE_MANAGE)
        remote(config, ["python3", REMOTE_MANAGE, "install-update", incoming])
        remote(config, ["python3", "-c", "from pathlib import Path; Path(" + repr(incoming) + ").unlink()"])


def push_input(config, folder, chapter):
    source = Path(folder).resolve()
    if not source.is_dir():
        raise RuntimeError("Input image folder not found")
    if not chapter or chapter in {".", ".."} or any(c in chapter for c in "/\\\r\n"):
        raise RuntimeError("Chapter must be a single folder name")
    images = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"})
    if not images:
        raise RuntimeError("Folder contains no supported images")
    hashes = {image.name: hashlib.sha256(image.read_bytes()).hexdigest() for image in images}
    destination = REMOTE_RUNTIME + "/data/input/" + chapter
    check = ("from pathlib import Path; import json; "
             f"dst=Path({destination!r}); meta=dst/'.input-manifest.json'; "
             "print(json.dumps({'exists':dst.exists(),'hashes':json.loads(meta.read_text()) if meta.exists() else None}))")
    previous = json.loads(remote(config, ["python3", "-c", check], capture=True))
    if previous["exists"]:
        if previous["hashes"] == hashes:
            print("Input images unchanged; reusing " + destination, flush=True)
            return destination
        raise RuntimeError("Chapter already has different images; choose another chapter folder name")
    with tempfile.TemporaryDirectory(prefix="manga-input-") as temp:
        archive = Path(temp) / "input.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
            for image in images:
                output.write(image, image.name)
            output.writestr(".input-manifest.json", json.dumps(hashes))
        incoming = REMOTE_RUNTIME + "/incoming/" + uuid.uuid4().hex + ".zip"
        remote(config, ["mkdir", "-p", REMOTE_RUNTIME + "/incoming"])
        upload(config, archive, incoming)
        script = ("from pathlib import Path; import zipfile; "
                  f"src=Path({incoming!r}); dst=Path({destination!r}); "
                  "assert not dst.exists(), 'Chapter already exists; use another name'; "
                  "z=zipfile.ZipFile(src); "
                  "assert all(Path(n).name==n and n not in ('.','..') for n in z.namelist()), 'Unsafe path'; "
                  "dst.mkdir(parents=True); z.extractall(dst); z.close(); src.unlink(); print(dst)")
        remote(config, ["python3", "-c", script])
    return destination


def main(argv=None):
    tokens = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect", help="Save SSH-over-TCP connection details locally")
    connect.add_argument("--host", required=True)
    connect.add_argument("--port", required=True, type=int)
    connect.add_argument("--key", required=True, type=Path)
    connect.add_argument("--pod-id", default="")
    commands.add_parser("sync", help="Update code atomically; no dependency installation")
    commands.add_parser("setup", help="Install/reuse the isolated environment")
    commands.add_parser("start", help="Start/reuse the managed Ollama server")
    commands.add_parser("status")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--inference", action="store_true")
    commands.add_parser("prefetch")
    commands.add_parser("jobs")
    logs = commands.add_parser("logs")
    logs.add_argument("job_id")
    push = commands.add_parser("input", help="Upload a new chapter image folder")
    push.add_argument("folder", type=Path)
    push.add_argument("--chapter", required=True)
    for name in ["run", "extract", "resume", "reanalyze", "translate", "review"]:
        pipeline = commands.add_parser(name, help="Run this pipeline command on the Pod")
        pipeline.add_argument("arguments", nargs=argparse.REMAINDER)
    fetch = commands.add_parser("fetch", help="Download outputs or banks without overwriting existing folders")
    fetch.add_argument("kind", choices=["outputs", "banks"])
    fetch.add_argument("destination", type=Path)
    passthrough = {"run", "extract", "resume", "reanalyze", "translate", "review"}
    if tokens and tokens[0] in passthrough and tokens[1:] not in [["--help"], ["-h"]]:
        args = argparse.Namespace(command=tokens[0], arguments=tokens[1:])
    else:
        args = parser.parse_args(tokens)
    try:
        if args.command == "connect":
            STATE.mkdir(parents=True, exist_ok=True)
            if CONNECTION.exists():
                CONNECTION.replace(STATE / "connection.previous.json")
            CONNECTION.write_text(json.dumps({"host": args.host, "port": args.port,
                                              "key": str(args.key.resolve()), "user": "root",
                                              "pod_id": args.pod_id}, indent=2) + "\n", encoding="utf-8")
            load_connection()
            print("Connection saved in .runpod/connection.json", flush=True)
            return 0
        config = load_connection()
        if args.command == "sync":
            sync(config)
        elif args.command == "input":
            push_input(config, args.folder, args.chapter)
        elif args.command == "fetch":
            fetch_data(config, args.kind, args.destination)
        else:
            command = ["python3", REMOTE_MANAGE, args.command]
            if args.command == "logs":
                command.append(args.job_id)
            if args.command == "doctor" and args.inference:
                command.append("--inference")
            if args.command in {"run", "extract", "resume", "reanalyze", "translate", "review"}:
                if not args.arguments:
                    raise RuntimeError("Specify the image folder or --run-dir for this command")
                sync(config)
                pipeline_args = list(args.arguments)
                if args.command in {"run", "extract"} and Path(pipeline_args[0]).is_dir():
                    chapter = Path(pipeline_args[0]).name
                    if "--chapter" in pipeline_args:
                        index = pipeline_args.index("--chapter")
                        if index + 1 >= len(pipeline_args):
                            raise RuntimeError("--chapter needs a value")
                        chapter = pipeline_args[index + 1]
                    pipeline_args[0] = push_input(config, pipeline_args[0], chapter)
                command = ["python3", REMOTE_MANAGE, "launch", args.command, *pipeline_args]
            remote(config, command)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Check Pod status before retrying a long-running command.", flush=True)
        return 130
    except Exception as exc:
        print("ERROR: " + str(exc), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
