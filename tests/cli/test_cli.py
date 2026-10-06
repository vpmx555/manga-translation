import unittest
from tests.helpers import make_store
from manga_pipeline.cli.main import build_parser
from manga_pipeline.extraction.pages import choose_device
from manga_pipeline.pipeline import PROJECT
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_device_contract(self):
        self.assertEqual(choose_device("auto", False), "cpu")
        self.assertEqual(choose_device("auto", True), "cuda")
        self.assertEqual(choose_device("cpu", True), "cpu")
        with self.assertRaises(RuntimeError):
            choose_device("cuda", False)

    def test_commands_and_defaults(self):
        args = build_parser().parse_args(["run", "images", "--story", "My Story", "--device", "cuda"])
        self.assertEqual(args.device, "cuda")
        self.assertEqual(PROJECT, Path(__file__).resolve().parents[2])
        args = build_parser().parse_args(["resume", "--run-dir", "outputs/test"])
        self.assertIsNone(args.until)
