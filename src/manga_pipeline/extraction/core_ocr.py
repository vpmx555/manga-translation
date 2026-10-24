"""Thanhdat-style crop OCR engine with the main pipeline's pinned assets."""


class MangaOcrEngine:
    def __init__(self, *, model_name, revision, processor_name, processor_revision,
                 device, max_num_patches=384, max_new_tokens=128):
        self.model_name = model_name
        self.revision = revision
        self.processor_name = processor_name
        self.processor_revision = processor_revision
        self.device = device
        self.max_num_patches = max_num_patches
        self.max_new_tokens = max_new_tokens
        self.model = self.processor = self.tokenizer = None
        self._is_loaded = False

    def load(self):
        if self._is_loaded:
            return
        from transformers import AutoModel, AutoProcessor, PreTrainedTokenizerFast
        from .hayai import local_assets
        model_source = local_assets(self.model_name, self.revision)
        self.model = AutoModel.from_pretrained(model_source, revision=self.revision,
            trust_remote_code=True).to(self.device).eval()
        self.tokenizer = PreTrainedTokenizerFast.from_pretrained(model_source, revision=self.revision)
        self.processor = AutoProcessor.from_pretrained(local_assets(self.processor_name, self.processor_revision),
            revision=self.processor_revision)
        self._is_loaded = True

    def predict_batch(self, images):
        """Preserve crop order and normalize whitespace as in thanhdat's core."""
        if not images:
            return []
        import torch
        self.load()
        inputs = None
        try:
            inputs = self.processor(images=[image.convert('RGB') for image in images],
                max_num_patches=self.max_num_patches, return_tensors='pt').to(self.device)
            with torch.inference_mode():
                predictions = self.model.generate(pixel_values=inputs['pixel_values'],
                    pixel_attention_mask=inputs['pixel_attention_mask'], spatial_shapes=inputs['spatial_shapes'],
                    tokenizer=self.tokenizer, max_new_tokens=self.max_new_tokens, repetition_penalty=1.0)
        except Exception:
            # Release the batch tensor reference before the caller retries OOM.
            inputs = None
            raise
        if isinstance(predictions, str) and len(images) == 1:
            predictions = [predictions]
        if (not isinstance(predictions, (list, tuple)) or len(predictions) != len(images)
                or any(not isinstance(text, str) for text in predictions)):
            raise ValueError('Hayai OCR returned invalid crop/text alignment')
        return [' '.join(text.split()) for text in predictions]
