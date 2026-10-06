"""Resolve the Linux/CUDA dependency set on Windows before renting a GPU."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen
import zipfile

PROJECT = Path(__file__).resolve().parents[2]
STATE = PROJECT / ".runpod"
VERSION = "0.12.23"
DIGEST = "75d05de6762778c31ee183398de7dd15093fad0ed90b1f236d8205ea5ec00c90"


def main():
    if sys.platform != "win32":
        raise RuntimeError("This preflight helper is for the Windows PC; Linux setup validates its own environment")
    directory = STATE / "tools" / ("uv-" + VERSION)
    binary = directory / "uv.exe"
    directory.mkdir(parents=True, exist_ok=True)
    if not binary.exists():
        archive = directory / "uv.zip"
        checksum = hashlib.sha256()
        request = Request(f"https://github.com/astral-sh/uv/releases/download/{VERSION}/uv-x86_64-pc-windows-msvc.zip",
                          headers={"User-Agent": "manga-runpod-preflight"})
        print("Downloading pinned uv for dependency preflight", flush=True)
        with urlopen(request, timeout=120) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                checksum.update(chunk)
        if checksum.hexdigest() != DIGEST:
            raise RuntimeError("uv checksum mismatch")
        with zipfile.ZipFile(archive) as package:
            names = [name for name in package.namelist() if Path(name).name == "uv.exe"]
            if len(names) != 1:
                raise RuntimeError("Unexpected uv archive layout")
            binary.write_bytes(package.read(names[0]))
    log = STATE / "dependency-validation.log"
    locked = STATE / "linux-requirements.lock"
    command = [str(binary), "pip", "compile", str(PROJECT / "deployment/runpod/requirements.in"),
               "--python-version", "3.12.11", "--python-platform", "x86_64-manylinux_2_28",
               "--index-strategy", "unsafe-best-match", "--generate-hashes", "--output-file", str(locked)]
    with log.open("wb") as output:
        result = subprocess.run(command, cwd=PROJECT, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        print(log.read_text(encoding="utf-8", errors="replace")[-6000:])
        raise RuntimeError("Linux dependencies did not resolve; see .runpod/dependency-validation.log")
    requirements_digest = hashlib.sha256((PROJECT / "deployment/runpod/requirements.in").read_bytes()).hexdigest()
    report = {"python": "3.12.11", "platform": "x86_64-manylinux_2_28",
              "requirements_sha256": requirements_digest, "lock_sha256": hashlib.sha256(locked.read_bytes()).hexdigest()}
    (STATE / "dependency-validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Linux CUDA dependencies resolved successfully; validation files are under .runpod/", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), flush=True)
        raise SystemExit(1)
