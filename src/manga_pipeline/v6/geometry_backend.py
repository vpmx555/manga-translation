"""Explicit CUDA array backend for geometry; CPU remains available for CPU runs."""
from contextlib import contextmanager

import numpy as np
from scipy import ndimage


def array_module(array):
    if hasattr(array, "__cuda_array_interface__"):
        import cupy
        return cupy
    return np


def image_operations(array):
    if hasattr(array, "__cuda_array_interface__"):
        from cupyx.scipy import ndimage as cuda_ndimage
        return cuda_ndimage
    return ndimage


def host_array(array):
    xp = array_module(array)
    return array if xp is np else xp.asnumpy(array)


class GeometryBackend:
    def __init__(self, device="cpu"):
        if device in {None, "auto"}:
            # The legacy auto mode remains import-safe for CPU/fake-model runs.
            # Managed Runpod jobs freeze device=cuda explicitly.
            device = "cpu"
        if device not in {"cpu", "cuda"}:
            raise ValueError("Bubble geometry device must be cpu or cuda")
        self.device = device
        self.xp = np
        self.receipt = {"device": "cpu", "engine": "numpy-scipy"}
        if device == "cuda":
            try:
                import cupy
                from cupyx.scipy import ndimage as cuda_ndimage
                if cupy.cuda.runtime.getDeviceCount() < 1:
                    raise RuntimeError("No CUDA device available")
                self.xp = cupy
                self.ordinal = cupy.cuda.runtime.getDevice()
                self.pool = cupy.cuda.MemoryPool()
                # Import the required kernels before reading/processing any page.
                for name in ("label", "binary_closing", "binary_opening", "binary_fill_holes",
                             "binary_erosion", "binary_dilation", "distance_transform_edt"):
                    if not callable(getattr(cuda_ndimage, name, None)):
                        raise RuntimeError("Missing CuPy geometry operation: " + name)
                self.receipt = {"device": "cuda", "ordinal": self.ordinal,
                                "engine": "cupyx.scipy.ndimage", "cupy_version": cupy.__version__}
            except Exception as exc:
                raise RuntimeError("CUDA bubble geometry is unavailable; install the pinned "
                                   "cupy-cuda12x dependency and verify CUDA. CPU fallback is disabled.") from exc

    @contextmanager
    def session(self):
        if self.device == "cpu":
            yield self
            return
        # Use a private page allocator so geometry does not retain model-sized
        # buffers in CuPy's global pool before the following inference stages.
        with self.xp.cuda.Device(self.ordinal), self.xp.cuda.using_allocator(self.pool.malloc):
            try:
                yield self
            finally:
                self.xp.cuda.get_current_stream().synchronize()
                self.pool.free_all_blocks()

    def build_groups(self, *args):
        from .visual import _build_groups
        root, directory, page, candidates, masks, pixels, config = args
        with self.session():
            return _build_groups(root, directory, page, candidates,
                                 [self.xp.asarray(mask) for mask in masks], pixels, config)


def preflight(device="cuda"):
    """Small kernel check for the existing remote doctor, without model inference."""
    backend = GeometryBackend(device)
    with backend.session():
        xp = backend.xp
        mask = xp.zeros((5, 5), dtype=bool)
        mask[2, 2] = True
        operations = image_operations(mask)
        expanded = operations.binary_dilation(mask, iterations=2, brute_force=True)
        _, count = operations.label(expanded)
        distances = operations.distance_transform_edt(~expanded)
        if int(expanded.sum()) != 13 or int(count) != 1 or not bool(xp.isfinite(distances).all()):
            raise RuntimeError("Geometry kernel preflight failed")
        report = {**backend.receipt, "kernel_verified": True, "distance_dtype": str(distances.dtype)}
        del mask, expanded, distances
        return report
