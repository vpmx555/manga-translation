"""Geometry backend contracts; CUDA parity runs only on an available GPU."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from manga_pipeline.v6.config import DEFAULT_CONFIG
from manga_pipeline.v6.geometry_backend import GeometryBackend, preflight
from manga_pipeline.v6.visual import build_groups


class GeometryBackendTests(unittest.TestCase):
    def test_cpu_session_and_kernel_preflight(self):
        report = preflight("cpu")
        self.assertEqual(report["device"], "cpu")
        self.assertTrue(report["kernel_verified"])
        self.assertEqual(report["distance_dtype"], "float64")

    def test_cuda_never_falls_back_when_dependency_is_missing(self):
        with patch.dict("sys.modules", {"cupy": None}):
            with self.assertRaisesRegex(RuntimeError, "CPU fallback is disabled"):
                GeometryBackend("cuda")

    @unittest.skipUnless(os.environ.get('MANGA_TEST_CUDA') == '1', 'Explicit CUDA qualification opt-in required')
    def test_gpu_matches_cpu_topology_masks_and_receipts(self):
        try:
            import cupy
        except ImportError:
            self.skipTest("CuPy is not installed")
        if cupy.cuda.runtime.getDeviceCount() < 1:
            self.skipTest("No CUDA device")
        gpu = GeometryBackend("cuda")
        mask = np.zeros((80, 112), dtype=bool)
        mask[10:55, 10:45] = True
        mask[31:34, 45:65] = True  # A narrow observed bridge.
        mask[14:52, 65:94] = True
        mask[26:30, 25:29] = False  # An enclosed hole.
        mask[61:64, 12:15] = True  # A small lobe exercises the exact EDT.
        nested = np.zeros_like(mask)
        nested[12:53, 12:43] = True
        pixels = np.full((*mask.shape, 3), 255, dtype=np.uint8)
        page = {"id": "fixture:p1", "panels": [[0, 0, 112, 80]], "tails": []}
        candidates = [{"id": "outer"}, {"id": "contained"}]
        config = {**DEFAULT_CONFIG, "min_body_area": 50}

        def normalized(value):
            if isinstance(value, dict):
                return {k: Path(v).name if k == "mask_path" else normalized(v)
                        for k, v in value.items()}
            if isinstance(value, list):
                return [normalized(v) for v in value]
            return value

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cpu_groups, cpu_files = build_groups(root, root / "cpu", page, candidates,
                                                [mask, nested], pixels, copy.deepcopy(config))
            gpu_groups, gpu_files = build_groups(root, root / "gpu", page, candidates,
                                                [mask, nested], pixels, copy.deepcopy(config), backend=gpu)
            self.assertEqual(normalized(cpu_groups), normalized(gpu_groups))
            self.assertEqual({Path(k).name: v for k, v in cpu_files.items()},
                             {Path(k).name: v for k, v in gpu_files.items()})
            # Artifact payloads must contain no device arrays or NumPy scalars.
            json.dumps(gpu_groups)
            self.assertEqual(gpu.pool.used_bytes(), 0)
            self.assertEqual(gpu.pool.total_bytes(), 0)


if __name__ == "__main__":
    unittest.main()
