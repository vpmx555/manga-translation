"""Small text-only GLiNER; source offsets and OCR spelling stay unchanged."""
from pathlib import Path
import re
import math

PRONOUNS = {"i", "me", "my", "mine", "we", "us", "our", "ours", "you", "your", "yours",
            "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them", "their", "theirs"}
HONORIFICS = {"san", "kun", "chan", "sama", "sensei", "senpai", "kohai", "dono"}
HONORIFIC_TAIL = re.compile(r"\s*[-–—]\s*(?:san|kun|chan|sama|sensei|senpai|kohai|dono)\b", re.I)


class GlinerNames:
    def __init__(self, config, *, cache_dir=None):
        self.config = dict(config)
        self.cache_dir = Path(cache_dir or Path(__file__).resolve().parents[3] / "models" / "ner")
        self.model = None
        self.device = None

    def load(self):
        if self.model is not None:
            return
        try:
            import torch
            from gliner import GLiNER
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError("GLiNER is missing; install requirements-names.txt in this Python environment") from exc
        device = self.config["device"]
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("GLiNER CUDA requested but PyTorch CUDA is unavailable")
        # Download one weight file rather than all fp32/fp16/bf16 variants.
        model_path = snapshot_download(
            repo_id=self.config["model"], revision=self.config["revision"], cache_dir=str(self.cache_dir),
            allow_patterns=["gliner_config.json", "pytorch_model.bin", "tokenizer*.json",
                            "added_tokens.json", "special_tokens_map.json", "spm.model"])
        self.model = GLiNER.from_pretrained(model_path, load_tokenizer=True,
                                            cache_dir=str(self.cache_dir), map_location=device)
        self.model.eval()
        self.device = device

    def close(self):
        import gc
        self.model = None
        gc.collect()
        if self.device == "cuda":
            import torch
            torch.cuda.empty_cache()

    def detect(self, text):
        self.load()
        import torch
        with torch.inference_mode():
            entities = self.model.predict_entities(text, ["person name"], threshold=self.config["threshold"])
        result = []
        for entity in entities:
            start, end = entity["start"], entity["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                raise ValueError("GLiNER returned invalid source offsets")
            confidence = float(entity["score"])
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("GLiNER returned invalid confidence")
            # Entity candidates are names, not English pronouns or honorifics alone.
            # This does not filter utterances or add any model call.
            name = text[start:end].strip()
            if name.casefold() in PRONOUNS | HONORIFICS or not any(c.isalpha() for c in name):
                continue
            tail = HONORIFIC_TAIL.match(text[end:])
            if tail:
                end += tail.end()
            result.append({"name": text[start:end], "start": start, "end": end,
                           "source": "gliner", "confidence": confidence})
        return result
