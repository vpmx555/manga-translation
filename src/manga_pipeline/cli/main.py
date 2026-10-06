import argparse
import sys
from pathlib import Path

from ..pipeline import PROJECT, Pipeline, create_run, load_config, reanalyze_run, translate_store
from ..progress import NullProgress, TerminalProgress
from ..storage.runs import ALL_STEPS, TRANSLATE_STEPS, RunStore


def build_parser():
    parser = argparse.ArgumentParser(description="MAGI + text semantics/name binding + Vietnamese translation; resumable pipeline v4 (v2/v3 compatible)")
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
            p.add_argument("--until", choices=TRANSLATE_STEPS, default=None)
    for command in ("resume", "review", *ALL_STEPS[1:]):
        help_text = "Write compact review files without model inference" if command == "review" else "Continue a saved run; completed targets are skipped"
        if command in {"classify", "link"}:
            help_text = "Legacy v2 run only; new runs use analyze"
        p = commands.add_parser(command, help=help_text)
        p.add_argument("--run-dir", required=True, type=Path)
        p.add_argument("--no-progress", action="store_true", help="Disable terminal progress bars")
        if command == "translate":
            p.add_argument("--config", type=Path, help="Translation/model settings for a new revision")
        if command == "resume":
            p.add_argument("--until", choices=ALL_STEPS, default=None)
            p.add_argument("--device", choices=("auto", "cpu", "cuda"), help="Only affects incomplete extraction")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
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
            until = args.until if args.command == "run" else "extract"
        else:
            store = RunStore(args.run_dir)
            if args.command == "translate":
                progress = NullProgress() if args.no_progress else TerminalProgress()
                try:
                    progress.start("translate", 1, 1)
                    output = translate_store(store, config=load_config(args.config) if args.config else None, progress=progress)
                    progress.finish("partial" if output["failures"] else "completed")
                finally:
                    progress.close()
                print(f"Translation: {output['directory']}")
                print(f"Reviews: {output['directory']}/review_natural.md, review_localized.md")
                return 2 if output["failures"] else 0
            if args.command == "review":
                from ..review.renderer import write_reviews
                from ..storage.locking import exclusive_lock
                with exclusive_lock(store.directory / ".run.lock", timeout=1):
                    path = write_reviews(RunStore(args.run_dir))
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
        success = Pipeline(store, progress=progress).run(until, only_step=args.command in ALL_STEPS[1:])
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
