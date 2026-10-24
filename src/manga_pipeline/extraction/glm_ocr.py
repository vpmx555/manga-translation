"""Pinned GLM-OCR recognition engine, imported only by the isolated OCR worker."""
import re
import tempfile
from pathlib import Path

MODEL = "zai-org/GLM-OCR"
REVISION = "2e85a62840ccac27daa451df36c736c4636b8628"
DEFAULT_CONFIG = {"backend": "glm-ocr", "model": MODEL, "revision": REVISION,
                  "engine": "core", "batch_size": 8, "max_num_patches": 384,
                  "max_new_tokens": 2048, "python": None}


def normalize(text):
    match = re.fullmatch(r"\s*```(?:markdown|text)?\s*\n(.*?)\n?```\s*", text, re.S | re.I)
    return " ".join((match.group(1) if match else text).split())


class GlmOcrEngine:
    def __init__(self, *, model_name, revision, device, max_new_tokens=2048):
        self.model_name, self.revision = model_name, revision
        self.device, self.max_new_tokens = device, max_new_tokens
        self.model = self.processor = None
        self.last_details = []

    def load(self):
        if self.model is not None:
            return
        import torch
        if not str(self.device).startswith("cuda") or not torch.cuda.is_available():
            raise RuntimeError("GLM-OCR requires CUDA; CPU/offload fallback is disabled")
        from transformers import AutoProcessor, GlmOcrForConditionalGeneration
        from .hayai import local_assets
        source = local_assets(self.model_name, self.revision)
        self.processor = AutoProcessor.from_pretrained(source, revision=self.revision)
        self.processor.tokenizer.padding_side = "left"
        self.model = GlmOcrForConditionalGeneration.from_pretrained(source, revision=self.revision,
            dtype=torch.bfloat16, attn_implementation="sdpa").eval().to(self.device)
        if any(p.device.type != "cuda" for p in self.model.parameters()):
            raise RuntimeError("GLM-OCR parameters are not entirely on CUDA")

    def predict_batch(self, images):
        if not images:
            self.last_details = []
            return []
        import torch
        self.load()
        inputs = None
        try:
            with tempfile.TemporaryDirectory(prefix="glm-crops-") as folder:
                messages = []
                for index, image in enumerate(images):
                    crop = Path(folder) / f"{index}.png"
                    image.convert("RGB").save(crop)
                    messages.append([{"role": "user", "content": [
                        {"type": "image", "url": str(crop.resolve())},
                        {"type": "text", "text": "Text Recognition:"}]}])
                inputs = self.processor.apply_chat_template(messages, tokenize=True,
                    add_generation_prompt=True, padding=True, return_dict=True, return_tensors="pt").to(self.device)
                width = inputs["input_ids"].shape[1]
                with torch.inference_mode():
                    generated = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens,
                                                   do_sample=False, return_dict_in_generate=False)
                new_ids = generated[:, width:]
                decoded = self.processor.batch_decode(new_ids, skip_special_tokens=True,
                                                       clean_up_tokenization_spaces=False)
                if len(decoded) != len(images) or any(not isinstance(t, str) for t in decoded):
                    raise ValueError("GLM-OCR crop/text alignment changed")
                eos = self.model.generation_config.eos_token_id
                eos = {eos} if isinstance(eos, int) else set(eos or [])
                details = []
                for text, ids in zip(decoded, new_ids.tolist()):
                    length = next((i for i, token in enumerate(ids) if token in eos), len(ids))
                    details.append({"raw_text": text, "text": normalize(text), "output_tokens": length,
                                    "truncated": length >= self.max_new_tokens,
                                    "empty": not normalize(text)})
                if any(item["truncated"] for item in details):
                    raise ValueError("GLM-OCR output truncated; no partial text accepted")
                self.last_details = details
                return [item["text"] for item in details]
        finally:
            inputs = None
