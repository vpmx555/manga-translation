"""Prepare a v6 job offline, or explicitly launch it on an existing authorized Pod."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import manage
import sync
from batch_v6 import safe_key

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def prepared(plan_path):
    plan = manage.read_json(plan_path)
    if not plan or plan.get("pipeline_version") != 6 or not safe_key(plan.get("batch")):
        raise ValueError("Invalid prepared v6 plan")
    if manage.file_hash(plan_path.parent / "pipeline-v6.json") != plan["config_sha256"]:
        raise ValueError("Prepared config changed; prepare a new job")
    with tempfile.TemporaryDirectory(prefix="v6-source-") as temporary:
        actual = sync.bundle(Path(temporary) / "code.zip")["version"]
    if actual != plan["source_bundle_hash"]:
        raise ValueError("Source changed after preparation; prepare a new job before renting/launching")
    return plan


def prepare(args):
    if not safe_key(args.batch) or not safe_key(args.story) or not safe_key(args.chapter):
        raise ValueError("Batch/story/chapter must be lowercase ASCII keys")
    source = args.folder.resolve()
    images = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED)
    if not images:
        raise ValueError("Source contains no supported chapter images")
    if not args.execution_root.startswith("/") or ".." in Path(args.execution_root).parts:
        raise ValueError("Execution root must be an absolute Linux path")
    destination = sync.STATE / "v6" / args.batch
    if destination.exists():
        raise ValueError("Prepared job already exists; choose a new batch ID")
    config = (sync.PROJECT / "configs/pipeline-v6-runpod.json").read_bytes()
    receipts = {p.name: {"bytes": p.stat().st_size, "sha256": manage.file_hash(p)} for p in images}
    destination.mkdir(parents=True)
    bundle = sync.bundle(destination / "source.zip")
    (destination / "pipeline-v6.json").write_bytes(config)
    plan = {"pipeline_version": 6, "batch": args.batch, "execution_root": args.execution_root,
            "source_bundle_hash": bundle["version"], "config_sha256": hashlib.sha256(config).hexdigest(),
            "chapters": [{"story": args.story, "story_key": args.story, "chapter": args.chapter,
                          "input": args.story + "-" + args.chapter + "-v6", "pages": len(images),
                          "source_folder": str(source), "images": receipts}]}
    manage.atomic_json(destination / "plan.json", plan)
    print(json.dumps({"plan": str(destination / "plan.json"), "pages": len(images),
                      "bytes": sum(p["bytes"] for p in receipts.values()),
                      "status": "prepared_offline", "runpod_accessed": False}, ensure_ascii=False))


REMOTE_LAUNCH = r'''
import json, os, pathlib, subprocess, sys, time
project=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(project/'deployment/runpod'))
import manage
plan=json.loads(sys.argv[2])
config=sys.argv[3]
directory=manage.RUNTIME/'jobs'/plan['batch']
with manage.lock('launch'):
    directory.mkdir(parents=True,exist_ok=True)
    state=manage.read_json(directory/'state.json',{})
    previous=manage.read_json(directory/'launch.json',{})
    identity=state.get('pid') or previous.get('identity')
    if identity and manage.service_identity(identity['pid']) == identity:
        raise RuntimeError('This batch still has an active driver')
    old=manage.read_json(directory/'plan.json')
    if old is not None and old != plan:
        raise RuntimeError('Cannot replace an immutable remote plan')
    path=directory/'pipeline-v6.json'
    if path.exists() and path.read_bytes().decode('utf-8') != config:
        raise RuntimeError('Cannot replace an immutable remote config')
    manage.atomic_json(directory/'plan.json',plan)
    if not path.exists():
        temporary=path.with_suffix('.json.pending')
        temporary.write_text(config,encoding='utf-8')
        temporary.replace(path)
    env=manage.environment()
    with (directory/'driver.log').open('ab') as log:
        process=subprocess.Popen([str(manage.current_python()),str(project/'deployment/runpod/batch_v6.py'),
                                  str(directory/'plan.json'),'--state',str(directory/'state.json')],
                                 cwd=project,env=env,stdin=subprocess.DEVNULL,
                                 stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    identity=manage.service_identity(process.pid)
    manage.atomic_json(directory/'launch.json',{'identity':identity,'started_at':time.time(),
                                              'source_bundle_hash':plan['source_bundle_hash']})
    print(json.dumps({'batch':plan['batch'],'status':'starting','driver_pid':process.pid,
                      'state':str(directory/'state.json'),'log':str(directory/'driver.log')}))
'''


def connection_for(pod_id):
    connection = sync.load_connection()
    if not pod_id or connection.get("pod_id") != pod_id:
        raise ValueError("Explicit Pod ID does not match the saved connection")
    return connection


def launch(args):
    plan = prepared(args.plan.resolve())
    connection = connection_for(args.pod_id)
    bound = {**plan, "pod_id": args.pod_id}
    bound_path = args.plan.resolve().parent / "bound-plan.json"
    previous = manage.read_json(bound_path)
    if previous is not None and previous != bound:
        raise ValueError("Job is already bound to another Pod")
    # This command never provisions, starts, stops or deletes a Pod, or installs dependencies.
    manage.atomic_json(bound_path, bound)
    sync.sync(connection)
    for entry in plan["chapters"]:
        source = Path(entry["source_folder"])
        actual = {p.name: {"bytes": p.stat().st_size, "sha256": manage.file_hash(p)}
                  for p in source.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED}
        if actual != entry["images"]:
            raise ValueError("Local input changed after preparation: " + entry["input"])
        sync.push_input(connection, source, entry["input"])
    config = (args.plan.resolve().parent / "pipeline-v6.json").read_bytes().decode("utf-8")
    # Pass the same execution root used by setup/doctor, through env on every remote process.
    result = sync.remote(connection, ["env", "MANGA_EXECUTION_ROOT=" + plan["execution_root"],
                                      "python3", "-c", REMOTE_LAUNCH, sync.REMOTE_PROJECT,
                                      json.dumps(bound), config], capture=True)
    manage.atomic_json(bound_path, bound)
    print(result.strip())
    print("BOUND_PLAN " + str(bound_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    offline = commands.add_parser("prepare", help="Offline manifest/source preparation; no models or Runpod")
    offline.add_argument("folder", type=Path)
    offline.add_argument("--batch", required=True)
    offline.add_argument("--story", default="after-school")
    offline.add_argument("--chapter", default="chapter-001")
    offline.add_argument("--execution-root", default="/opt/manga-execution")
    start = commands.add_parser("launch", help="Explicit upload/launch on an already authorized existing Pod")
    start.add_argument("--plan", required=True, type=Path)
    start.add_argument("--pod-id", required=True)
    monitor = commands.add_parser("watch", help="One bounded read of a bound job")
    monitor.add_argument("--plan", required=True, type=Path)
    monitor.add_argument("--wait", type=int, default=0)
    collect = commands.add_parser("collect", help="Download stopped complete/partial chapters and bank/audit")
    collect.add_argument("--plan", required=True, type=Path)
    collect.add_argument("--destination", required=True, type=Path)
    collect.add_argument("--poll", type=int, default=30)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args)
    elif args.command == "launch":
        launch(args)
    else:
        plan = manage.read_json(args.plan)
        if not plan or plan.get("pipeline_version") != 6 or not safe_key(plan.get("batch")):
            raise ValueError("Specify the bound-plan.json generated by launch")
        connection_for(plan.get("pod_id"))
        if args.command == "watch":
            if not 0 <= args.wait <= 45:
                raise ValueError("Wait must be 0..45 seconds")
            from watch import REMOTE
            print(sync.remote(sync.load_connection(), ["python3", "-c", REMOTE, plan["batch"],
                                                       str(args.wait)], capture=True).strip())
        else:
            from collect import main as collect_main
            destination = args.destination.resolve()
            return collect_main([str(args.plan.resolve()), "--outputs", str(destination / "outputs"),
                                 "--banks", str(destination / "banks"), "--audit", str(destination / "audit"),
                                 "--state", str(destination / "collection.json"), "--poll", str(args.poll)])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; inspect driver identity/checkpoints before retrying.")
        raise SystemExit(130)
    except Exception as exc:
        print("ERROR: " + str(exc))
        raise SystemExit(1)
