import argparse
import sys
from pathlib import Path

from ..pipeline import PROJECT, Pipeline, create_run, load_config, reanalyze_run
from ..progress import NullProgress, TerminalProgress
from ..storage.runs import ALL_STEPS, V6_STEPS, RunStore


def build_parser():
    parser = argparse.ArgumentParser(description="Koharu layout + MAGI + semantics/translation + independent inpaint; independent-batches v6")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("reanalyze", help="New essential-only run reusing completed MAGI extraction")
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--output-root", type=Path, default=PROJECT / "outputs")
    p.add_argument("--config", type=Path)
    p.add_argument("--until", choices=ALL_STEPS, default=None, help="Stop after this step; defaults to the run's final stage")
    p.add_argument("--no-progress", action="store_true")
    for command in ("run", "extract"):
        p = commands.add_parser(command, help="Start a new run from an image folder")
        p.add_argument("image_folder", type=Path)
        p.add_argument("--story", required=True)
        p.add_argument("--chapter", help="Defaults to image folder name")
        p.add_argument("--output-root", type=Path, default=PROJECT / "outputs")
        p.add_argument("--bank-root", type=Path, default=PROJECT / "banks")
        p.add_argument("--config", type=Path)
        p.add_argument("--device", choices=("auto", "cpu", "cuda"))
        p.add_argument("--no-visualizations", action="store_true")
        p.add_argument("--no-progress", action="store_true", help="Disable terminal progress bars")
        if command == "run":
            p.add_argument("--until", choices=V6_STEPS, default=None,
                           help="Run ancestors of this stage; default includes translation, inpaint is opt-in")
    for command in ("resume", "review", *(s for s in ALL_STEPS if s != "extract")):
        help_text = "Write compact review files without model inference" if command == "review" else "Continue a saved run; completed targets are skipped"
        p = commands.add_parser(command, help=help_text)
        p.add_argument("--run-dir", required=True, type=Path)
        p.add_argument("--no-progress", action="store_true", help="Disable terminal progress bars")
        if command == "translate":
            p.add_argument("--config", type=Path, help="Translation/model settings for a new revision")
        if command == "resume":
            p.add_argument("--until", choices=ALL_STEPS, default=None)
            p.add_argument("--device", choices=("auto", "cpu", "cuda"), help="Only affects incomplete extraction")
    p = commands.add_parser("person-revert", help="Revert the latest unchanged v6 person operation; MAGI bank stays intact")
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--operation", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "person-revert":
            from ..v6.persons import bank_snapshot, registry_path, revert
            store = RunStore(args.run_dir)
            if store.manifest["pipeline_version"] != 6:
                raise ValueError("person-revert requires a v6 run")
            before = bank_snapshot(store.manifest["bank_path"])
            revert(registry_path(store), args.operation)
            if before != bank_snapshot(store.manifest["bank_path"]):
                raise ValueError("Source bank changed while reverting person registry")
            print(f"Reverted person operation: {args.operation}")
            return 0
        if args.command == "reanalyze":
            store = reanalyze_run(args.run_dir, args.output_root, load_config(args.config))
            print(f"Run directory: {store.directory}", flush=True)
            until = args.until
        elif args.command in {"run", "extract"}:
            config = load_config(args.config, args.device)
            if args.no_visualizations:
                config["visualizations"] = False
            store = create_run(args.image_folder, args.story, args.chapter or args.image_folder.name,
                               args.output_root, args.bank_root, config)
            print(f"Run directory: {store.directory}", flush=True)
            until = args.until if args.command == "run" else "bubble_geometry"
        else:
            store = RunStore(args.run_dir)
            if args.command == "translate" and store.manifest["pipeline_version"] == 6 and args.config:
                raise ValueError("V6 config changes require a new run/reanalyze revision")
            if args.command == "review":
                from ..storage.locking import exclusive_lock
                with exclusive_lock(store.directory / ".run.lock", timeout=1):
                    from ..v6.stages import write_review
                    write_review(store)
                    path = store.directory / "review.md"
                print(f"Review: {path}")
                return 0
            until = args.until if args.command == "resume" else args.command
            if args.command == "resume" and args.device and not store.completed("extract"):
                from ..storage.locking import exclusive_lock
                with exclusive_lock(store.directory / ".run.lock", timeout=1):
                    store = RunStore(args.run_dir)
                    if not store.completed("extract"):
                        store.manifest["extraction_device"] = args.device
                        store.save()
        progress = NullProgress() if args.no_progress else TerminalProgress()
        success = Pipeline(store, progress=progress).run(until, only_step=args.command in ALL_STEPS and args.command != "extract")
        print(f"{'Completed' if success else 'Partial; resume to retry failed targets'}: {store.directory}")
        if (store.directory / "review.md").is_file():
            print(f"Review: {store.directory / 'review.md'}")
        return 0 if success else 2
    except KeyboardInterrupt:
        print("Interrupted; resume the same run directory to continue", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
