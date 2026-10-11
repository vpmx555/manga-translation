"""One inference per page; paste predictions only inside the original mask."""
import numpy as np
from PIL import Image
from .config import checked_assets
from ..extraction.pages import choose_device


class AOT:
    def __init__(self,config):
        import torch
        from safetensors.torch import load_file
        from .aot_model import AOTGenerator
        directory,self.asset_hashes = checked_assets(config)
        self.config = config
        self.device = choose_device(config["device"],torch.cuda.is_available())
        self.model = AOTGenerator()
        self.model.load_state_dict(load_file(str(directory/"model.safetensors"),device="cpu"),strict=True)
        self.model.to(self.device).eval()

    def predict(self,image,mask):
        import torch
        original,original_mask = image.copy(),mask.copy()
        scale = min(1.0,self.config["max_side"]/max(image.size))
        size = tuple(max(64,round(n*scale/8)*8) for n in image.size)
        work = image.resize(size,Image.Resampling.LANCZOS)
        work_mask = mask.resize(size,Image.Resampling.NEAREST)
        pixels = np.asarray(work,dtype=np.float32)/127.5-1
        binary = (np.asarray(work_mask)>0).astype(np.float32)
        tensor = torch.from_numpy(pixels).permute(2,0,1).unsqueeze(0).to(self.device)
        mask_tensor = torch.from_numpy(binary).unsqueeze(0).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            result = self.model(tensor*(1-mask_tensor),mask_tensor)[0].permute(1,2,0).cpu()
        prediction = Image.fromarray(((result+1)*127.5).clamp(0,255).byte().numpy())
        if prediction.size != original.size:
            prediction = prediction.resize(original.size,Image.Resampling.BICUBIC)
        original.paste(prediction,mask=original_mask)
        return original
