"""Reconstruction branch: clean pages selected by normalize, independent of analyze."""
from time import monotonic
import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation,binary_erosion
from .geometry import clipped_box,read_mask,write_png
from .stages import verify_files
from ..storage.io import file_hash


def boxes_mask(size,boxes):
    w,h = size
    mask = np.zeros((h,w),dtype=bool)
    for box in boxes:
        x1,y1,x2,y2 = clipped_box(box,size)
        mask[y1:y2,x1:x2] = True
    return mask


def retained_ids(document):
    ids = {key for r in document["utterances"] for key in r["source_text_ids"]}
    ids.update(r["source_text_id"] for r in document.get("translation_only",[]) if r.get("text","").strip())
    excluded = {r["source_text_id"] for r in document.get("excluded",[]) if r.get("source_text_id")}
    excluded.update(key for r in document.get("excluded",[]) for key in r.get("source_text_ids", []))
    if ids & excluded:
        raise ValueError("A source text cannot be both retained and excluded")
    return ids


def white_background(pixels,sample,config):
    colors = pixels[sample]
    if len(colors)<64:
        return False,{"reason":"insufficient_background_samples","samples":len(colors)}
    colors = colors.astype(float)
    bright = colors.min(axis=1)>=config["white_min"]
    neutral = np.ptp(colors,axis=1)<=config["white_chroma"]
    fraction = float(np.mean(bright & neutral))
    std = float(colors.std(axis=0).max())
    good = fraction>=config["white_fraction"] and std<=config["white_std"]
    return good,{"reason":"confident_white" if good else "nonwhite_or_uncertain",
                 "samples":len(colors),"white_fraction":round(fraction,4),"std":round(std,4)}


def plan_masks(image,page,texts,selected,root,config):
    pixels = np.asarray(image.convert("RGB"))
    all_text = boxes_mask(image.size,[r["bbox"] for r in texts])
    protected_text = boxes_mask(image.size,[r["bbox"] for r in texts if r["id"] not in selected])
    bubbles = {b["id"]:b for b in page.get("bubbles",[])}
    masks = {key:read_mask(root,b,image.size) for key,b in bubbles.items()}
    safe = {key:binary_erosion(mask,iterations=2) for key,mask in masks.items()}
    borders = np.zeros(all_text.shape,dtype=bool)
    for key,mask in masks.items():
        borders |= binary_dilation(mask,iterations=2) & ~safe[key]
    for group in page.get("bubble_groups", []):
        borders |= read_mask(root, group["bridge_mask"], image.size)
        borders |= read_mask(root, group["tail_mask"], image.size)
    white,aot = np.zeros(all_text.shape,dtype=bool),np.zeros(all_text.shape,dtype=bool)
    decisions = []
    for row in texts:
        if row["id"] not in selected:
            continue
        base = boxes_mask(image.size,[row["bbox"]])
        if not base.any():
            raise ValueError(f"Retained text has an empty bbox: {row['id']}")
        bubble_id = row.get("bubble_id")
        is_white,evidence = False,{"reason":"unmatched_bubble"}
        if bubble_id in masks:
            ring = binary_dilation(base,iterations=8) & ~binary_dilation(all_text,iterations=2) & safe[bubble_id]
            is_white,evidence = white_background(pixels,ring,config)
            # Never white-fill a bbox crossing the safe bubble interior.
            if np.any(base & ~safe[bubble_id]):
                is_white = False
                evidence["reason"] = "text_touches_bubble_boundary"
        if is_white:
            candidate = base
        else:
            candidate = binary_dilation(base,iterations=config["dilation"]) if config["dilation"] else base
        candidate = candidate & ~borders & ~protected_text
        if page.get("bubble_groups") and bubble_id in safe:
            candidate &= safe[bubble_id]
        clipped = bool(np.any(base & ~candidate))
        if not candidate.any():
            raise ValueError(f"Text erase mask is fully blocked by protected geometry: {row['id']}")
        if is_white:
            white |= candidate
        else:
            aot |= candidate
        decisions.append({"source_text_id":row["id"],"bubble_id":bubble_id,
                          "method":"white_fill" if is_white else "aot","background":evidence,
                          "requires_review":clipped,"reason":"protected_boundary_overlap" if clipped else None})
    # Keep white fills out of the AOT composite even when text masks overlap.
    aot &= ~white
    return white,aot,decisions


def inpaint(store,backend=None,progress=None):
    raw = store.read("bubble_geometry")
    is_v6 = store.manifest["pipeline_version"] == 6
    document = store.read("export" if is_v6 else "normalize")
    if is_v6:
        document = {**document, "utterances": [r for r in document["utterances"]
            if r.get("content_type") in {"dialogue", "narration"}]}
    config = store.manifest["config"]["inpaint"]
    selected = retained_ids(document)
    known = {r["id"] for r in raw["texts"]}
    if selected-known:
        raise ValueError("Normalize retained unknown source text IDs")
    pages,failures = [],[]
    if progress is not None:
        progress.reset(total=len(raw["pages"]),unit="page")
    for original_page in raw["pages"]:
        import copy
        page = copy.deepcopy(original_page)
        if is_v6:
            page["bubbles"] = [{**part["body_mask"], "id": part["id"]}
                for group in page.get("bubble_groups", []) for part in group["parts"]]
        texts = [r for r in raw["texts"] if r["page_id"]==page["id"]]
        if is_v6:
            texts = copy.deepcopy(texts)
            for text in texts:
                text["bubble_id"] = text.get("part_ids", [None])[0] if text.get("part_ids") else None
        source = store.manifest["source"]["images"][page["page"]-1]
        request = {"page":page,"texts":texts,"selected":sorted(selected & {r["id"] for r in texts}),
                   "source":source,"config":config,"policy":"inpaint-v6.1" if is_v6 else "inpaint-v1"}
        key,started = page["id"],monotonic()
        try:
            if file_hash(source["path"])!=source["sha256"]:
                raise ValueError("Source image changed")
            cached = store.target("inpaint",key,request)
            if cached is not None:
                verify_files(store.directory,cached)
                pages.append(cached)
                continue
            with Image.open(source["path"]) as im:
                image = im.convert("RGB")
            white,aot,decisions = plan_masks(image,page,texts,selected,store.directory,config)
            white_image = Image.fromarray(white.astype(np.uint8)*255)
            aot_image = Image.fromarray(aot.astype(np.uint8)*255)
            result = image.copy()
            result.paste((255,255,255),(0,0,image.width,image.height),white_image)
            calls = 0
            if aot.any():
                if backend is None:
                    from .aot import AOT
                    backend = AOT(config)
                prediction = backend.predict(result,aot_image)
                if prediction.size!=image.size:
                    raise ValueError("AOT output dimensions differ from input")
                # Protect every pixel outside the mask even with a faulty backend.
                result.paste(prediction,mask=aot_image)
                calls = 1
            directory = store.artifact_path("inpaint").parent/"pages"/f"page-{page['page']}"
            files = {}
            for name,value in (("clean.png",result),("white_mask.png",white_image),("aot_mask.png",aot_image)):
                path = directory/name
                write_png(path,value)
                files[path.relative_to(store.directory).as_posix()] = file_hash(path)
            cached = {"page_id":page["id"],"page":page["page"],"source":source,"files":files,
                      "clean_path":(directory/"clean.png").relative_to(store.directory).as_posix(),
                      "model_asset_sha256":getattr(backend,"asset_hashes",{}).get("model.safetensors") if calls else None,
                      "decisions":decisions,"aot_calls":calls,
                      "runtime_seconds":round(monotonic()-started,6)}
            store.commit_target("inpaint",key,request,result=cached)
            pages.append(cached)
        except Exception as exc:
            store.commit_target("inpaint",key,request,error=str(exc))
            failures.append({"page":page["page"],"error":str(exc)})
        finally:
            if progress is not None:
                progress.advance()
    return {"policy":"inpaint-v6.1" if is_v6 else "inpaint-v1","model":config["model"],"revision":config["revision"],
            "config":config,"coordinate_space":"original_page_pixels",
            "pages":pages,"failures":failures,"expected_pages":len(raw["pages"]),
            "completed_pages":len(pages),"selected_texts":len(selected)}
