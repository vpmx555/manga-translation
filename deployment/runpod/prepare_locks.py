"""Resolve Linux locks on an existing local CPU container, before renting a GPU."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

import manage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Explicit pinned CPU preparation image; required on Windows")
    args = parser.parse_args()
    if os.name == 'nt':
        if not args.image or args.image.endswith(':latest'):
            parser.error('--image requires the explicitly selected pinned preparation image')
        project = Path(__file__).resolve().parents[2]
        linux_path = subprocess.check_output(['wsl.exe', '--', 'wslpath', '-a', project.as_posix()], text=True).strip()
        subprocess.run(['wsl.exe', '--', 'docker', 'run', '--rm', '--name', 'manga-v6-lock-prepare',
                        '--entrypoint', 'python3', '-v', linux_path + ':/prepared',
                        args.image,
                        '/prepared/deployment/runpod/prepare_locks.py'], check=True)
        return
    root = manage.PROJECT / '.runpod/v6-locks' / manage.dependency_signature()
    root.mkdir(parents=True, exist_ok=True)
    uv = str(manage.ensure_uv())
    python = str(manage.current_python())
    pending = root / 'requirements.lock.pending'
    subprocess.run([uv, 'pip', 'compile', str(manage.PROJECT / 'deployment/runpod/requirements.in'),
                    '--python', python, '--index-strategy', 'unsafe-best-match', '--generate-hashes',
                    '--output-file', str(pending)], check=True, stdout=subprocess.DEVNULL)
    pending.replace(root / 'requirements.lock')
    vllm_root = root.parent / 'vllm'
    vllm_root.mkdir(parents=True, exist_ok=True)
    vllm_pending = vllm_root / 'requirements.lock.pending'
    subprocess.run([uv, 'pip', 'compile', str(manage.PROJECT / 'deployment/runpod/requirements-vllm.in'),
                    '--python', python, '--generate-hashes', '--output-file', str(vllm_pending)],
                   check=True, stdout=subprocess.DEVNULL)
    vllm_pending.replace(vllm_root / 'requirements.lock')
    glm_root = root.parent / manage.glm_python().parents[2].name
    glm_root.mkdir(parents=True, exist_ok=True)
    glm_pending = glm_root / 'requirements.lock.pending'
    subprocess.run([uv, 'pip', 'compile', str(manage.PROJECT / 'deployment/runpod/requirements-glm-ocr.in'),
                    '--python', python, '--index-strategy', 'unsafe-best-match', '--generate-hashes',
                    '--output-file', str(glm_pending)], check=True, stdout=subprocess.DEVNULL)
    glm_pending.replace(glm_root / 'requirements.lock')
    print('LOCK_READY ' + manage.dependency_signature(), flush=True)


if __name__ == '__main__':
    main()
