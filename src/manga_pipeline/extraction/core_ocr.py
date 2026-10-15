"""Hayai OCR engine for detected comic text regions."""

import logging
from pathlib import Path
from typing import List, Optional, Union
import torch
from PIL import Image

logger = logging.getLogger(__name__)

HAYAI_MODEL_DIRECTORY = (
    Path(__file__).resolve().parents[3] / "models" / "hayai-ocr-v2"
)
SIGLIP_PROCESSOR_DIRECTORY = (
    Path(__file__).resolve().parents[3] / "models" / "siglip2-base-patch16-naflex"
)


class MangaOcrEngine:
    def __init__(
        self,
        model_name: str = "JustANormalTinkerer/hayai-ocr-v2",
        device: Optional[str] = None,
        fp16: bool = False,
        model_directory=None,
        processor_directory=None,
        max_num_patches: int = 256,
        max_new_tokens: int = 128,
    ):
        self.model_name = model_name
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.fp16 = fp16 and (self.device == "cuda")
        self.model = None
        self.processor = None
        self.tokenizer = None
        self._is_loaded = False
        self.model_directory = Path(model_directory) if model_directory else HAYAI_MODEL_DIRECTORY
        self.processor_directory = Path(processor_directory) if processor_directory else SIGLIP_PROCESSOR_DIRECTORY
        self.max_num_patches = max_num_patches
        self.max_new_tokens = max_new_tokens
        self._managed_assets = model_directory is not None and processor_directory is not None

    def load(self):
        """Lazy loading of the model to conserve VRAM until needed."""
        if self._is_loaded:
            return

        required_files = (
            self.model_directory / "config.json",
            self.model_directory / "model.safetensors",
            self.model_directory / "configuration_hayai.py",
            self.model_directory / "modeling_hayai.py",
            self.model_directory / "tokenizer.json",
            self.model_directory / "tokenizer_config.json",
            self.processor_directory / "preprocessor_config.json",
        )
        if self._managed_assets:
            # v6 verifies the entire pinned asset receipt before constructing us.
            # Nova can use different remote-code names or sharded weights.
            required_files = (self.model_directory / "config.json",
                              self.processor_directory / "preprocessor_config.json")
        missing_files = [path for path in required_files if not path.is_file()]
        if missing_files:
            missing = ", ".join(str(path) for path in missing_files)
            raise FileNotFoundError(
                f"Hayai OCR model assets are missing: {missing}. "
                "Run `python setup_local_models.py` to download the required "
                "Hayai and SigLIP2 files."
            )

        try:
            from transformers import AutoModel, AutoProcessor, PreTrainedTokenizerFast
        except ImportError as error:
            raise RuntimeError(
                "Hayai OCR requires transformers. Install it with "
                "`pip install transformers`."
            ) from error

        logger.info("Loading Hayai OCR model on %s...", self.device)
        model = AutoModel.from_pretrained(
            str(self.model_directory),
            trust_remote_code=True,
            local_files_only=True,
        )
        processor = AutoProcessor.from_pretrained(
            str(self.processor_directory),
            local_files_only=True,
        )
        tokenizer = PreTrainedTokenizerFast.from_pretrained(
            str(self.model_directory),
            local_files_only=True,
        )
        self.model = model.to(self.device).eval()
        self.processor = processor
        self.tokenizer = tokenizer
        self._is_loaded = True
        logger.info("Hayai OCR loaded successfully.")

    def predict(self, image: Union[Image.Image, str]) -> str:
        """Run OCR on a single cropped image."""
        if isinstance(image, str):
            with Image.open(image) as source:
                image = source.convert("RGB")
        return self.predict_batch([image])[0]

    def predict_batch(self, images: List[Image.Image]) -> List[str]:
        """One model call per batch, preserving the original crop order."""
        if not images:
            return []
        self.load()
        inputs = self.processor(
            images=[image.convert("RGB") for image in images],
            max_num_patches=self.max_num_patches,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            predictions = self.model.generate(
                pixel_values=inputs["pixel_values"],
                pixel_attention_mask=inputs["pixel_attention_mask"],
                spatial_shapes=inputs["spatial_shapes"],
                tokenizer=self.tokenizer,
                max_new_tokens=self.max_new_tokens,
                repetition_penalty=1.0,
            )
        if isinstance(predictions, str) and len(images) == 1:
            predictions = [predictions]
        if (not isinstance(predictions, (list, tuple)) or len(predictions) != len(images)
                or any(not isinstance(text, str) for text in predictions)):
            raise RuntimeError("Hayai returned invalid crop/text alignment.")
        return [self._clean_english_text(text) for text in predictions]

    @staticmethod
    def _clean_english_text(text: str) -> str:
        """Normalize whitespace while preserving English word boundaries."""
        if not text:
            return ""
        return " ".join(text.split())
