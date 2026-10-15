"""AOT inference used by the v6 backend; weights are supplied after receipt verification."""
import numpy as np
import torch
from PIL import Image


def _aot_inpaint(image: Image.Image, mask: Image.Image, backend: str = "auto", *, model=None, max_side=1024) -> Image.Image:
    if backend == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA was requested but is unavailable in the active PyTorch environment. "
            "Install a CUDA-enabled PyTorch build or use backend='cpu'."
        )
    device = "cuda" if backend == "cuda" or (backend == "auto" and torch.cuda.is_available()) else "cpu"
    if model is None:
        raise ValueError("A verified AOT backend model must be supplied")
    original_image = image.copy()
    original_mask = mask.copy()
    original_size = image.size
    scale = min(1.0, max_side / max(original_size))
    inference_size = tuple(
        max(64, round(original_size[index] * scale / 8) * 8) for index in range(2)
    )
    if inference_size != original_size:
        image = image.resize(inference_size, Image.Resampling.LANCZOS)
        mask = mask.resize(inference_size, Image.Resampling.NEAREST)

    image_array = np.asarray(image, dtype=np.float32) / 127.5 - 1.0
    mask_array = (np.asarray(mask, dtype=np.uint8) > 0).astype(np.float32)
    height, width = mask_array.shape
    pad_height = (-height) % 8
    pad_width = (-width) % 8
    image_tensor = torch.from_numpy(image_array).permute(2, 0, 1).unsqueeze(0)
    mask_tensor = torch.from_numpy(mask_array).unsqueeze(0).unsqueeze(0)
    image_tensor = torch.nn.functional.pad(
        image_tensor, (0, pad_width, 0, pad_height), mode="reflect"
    ).to(device)
    mask_tensor = torch.nn.functional.pad(
        mask_tensor, (0, pad_width, 0, pad_height), mode="constant", value=0
    ).to(device)

    with torch.inference_mode():
        prediction = model(image_tensor * (1 - mask_tensor), mask_tensor)
    prediction = prediction[0, :, :height, :width].permute(1, 2, 0).cpu()
    prediction = ((prediction + 1) * 127.5).clamp(0, 255).byte().numpy()
    result = Image.fromarray(prediction, mode="RGB")

    if result.size != original_size:
        result = result.resize(original_size, Image.Resampling.BICUBIC)
    original_image.paste(result, mask=original_mask)
    return original_image
