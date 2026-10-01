"""End-to-end runner storage and resume contracts without loading models."""
from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dialogue_data import read_json, write_json
from run_chapter import ROOT, build_parser, run


class ChapterRunnerContracts(unittest.TestCase):
    def test_single_check_file_preserves_stages_and_resume_skips_magi(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as temporary:
            base = Path(temporary)
            images = base / "images"
            images.mkdir()
            (images / "01.png").write_bytes(b"fixture image")
            destination = base / "output"
            args = build_parser().parse_args([str(images), "--output-dir", str(destination),
                                              "--bank-root", str(base / "absent-bank")])
            def magi(command, **kwargs):
                raw_path = Path(command[command.index("--raw-json") + 1])
                normalized_path = Path(command[command.index("--normalized-json") + 1])
                bank = Path(command[command.index("--bank-root") + 1]) / "fixture"
                bank.mkdir(parents=True)
                write_json(bank / "metadata.json", {"characters": {}})
                write_json(raw_path, {"kind": "magi_raw", "story_id": "fixture", "texts": []})
                write_json(normalized_path, {"kind": "normalized_dialogue", "utterances": []})
            calls = []
            def reasoning(chapter, client, **kwargs):
                calls.append(True)
                kwargs["step_callback"]("3A", {"utterances": [{"speaker_id": None}]})
                if len(calls) == 1:
                    raise RuntimeError("Fixture interrupted after 3A")
                for step in ("3B", "3C", "3D", "complete"):
                    kwargs["step_callback"](step, {"utterances": []})
                return {"kind": "structured_dialogue", "utterances": []}
            with patch("run_chapter.subprocess.run", side_effect=magi) as extraction, \
                    patch("run_chapter.OllamaReasoner.prepare"), patch("run_chapter.load_chapter"), \
                    patch("run_chapter.run_reasoning", side_effect=reasoning):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    run(args)
                failed = read_json(destination / "check.json")
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["steps"]["stage1"]["output"]["kind"], "magi_raw")
                self.assertIn("3A", failed["steps"])
                args.resume = True
                final = run(args)
                self.assertEqual(extraction.call_count, 1)
            check = read_json(destination / "check.json")
            self.assertEqual(check["status"], "complete")
            self.assertNotIn("error", check)
            self.assertTrue({"stage1", "stage2_normalization", "3A", "3B", "3C", "3D", "complete"} <= check["steps"].keys())
            self.assertEqual(read_json(final)["kind"], "structured_dialogue")
            with self.assertRaises(FileExistsError):
                run(args)


if __name__ == "__main__":
    unittest.main()
