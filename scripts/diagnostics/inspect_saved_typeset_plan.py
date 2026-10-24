"""Explain a saved plan using cached glyph audit, without fitting or model calls."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "src"))

from PIL import Image, ImageFont
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.visual.stages import verify_files
from audit_typeset_overlap import crop_visual


def inspect(source,page_number,utterance,output):
    source,output = source.resolve(),output.resolve()
    if output.exists():
        raise ValueError("Use a new inspection directory")
    result = read_json(source / "result.json")
    page = next(p for p in result["pages"] if p["page"] == page_number)
    verify_files(source,page)
    plan_path = source / "plans" / f"page-{page_number:02d}.json"
    data = read_json(plan_path)
    row = data["plans"][utterance-1]
    if row.get("render_font_scale",1.0) != 1.0:
        raise ValueError("This inspection expects glyphs drawn at the plan font size")
    audit = read_json(source / "overlap-audit/analysis.json")
    if audit["source_result_sha256"] != file_hash(source / "result.json"):
        raise ValueError("Cached glyph audit no longer matches the source")
    targets = [r for page in audit["pages"] if page["page"] == page_number
        for r in page["regions"] if r["utterance"] == utterance]
    font_path = Path(data["font_path"])
    if file_hash(font_path) != data["font_sha256"]:
        raise ValueError("Recorded font changed")
    font = ImageFont.truetype(str(font_path),row["font_size_px"])
    image = Image.open(source / "pages" / f"page-{page_number:02d}.png").convert("RGB")
    output.mkdir(parents=True)
    parts,markdown = [],[f"# Plan trang {page_number}, utterance {utterance}", "",
        f"Source plan: `{plan_path.name}`; SHA-256 `{file_hash(plan_path)}`.", "",
        f"Font plan/vẽ: **{row['font_size_px']}px / {row.get('render_font_size_px',row['font_size_px'])}px**; scale 1.0; line_spacing **{data['config']['line_spacing']}**.",
        f"Text: `{row['translation']}`.", "",
        f"Số vùng dàn: {len(row['parts'])}; số vòng mở rộng: {len(row['expansion_steps'])}. Quota theo diện tích OCR ban đầu, không dùng toàn bộ bubble.", ""]
    for target,part in zip(targets,row["parts"]):
        line_records = []
        for index,line in enumerate(target["lines"]):
            baseline = line["y"]+line["bbox_lt"][1]-line["bbox_ls"][1]
            following = target["lines"][index+1] if index+1 < len(target["lines"]) else None
            gap = (following["global_ink_bbox"][1]-line["global_ink_bbox"][3]) if following else None
            next_baseline = (following["y"]+following["bbox_lt"][1]-following["bbox_ls"][1]) if following else None
            line_records.append({**line,"baseline_y":baseline,"ink_gap_to_next_px":gap,
                "baseline_step_to_next_px":next_baseline-baseline if following else None})
        visual = crop_visual(output,page_number,utterance,target["part"],part,part["lines"],font,image)
        parts.append({**target,"lines":line_records,"visual":visual})
        markdown.extend([f"## Vùng {target['part']}", "",
            f"OCR bbox gốc `{part['original_ocr_bbox']}`; bbox raster `{part['bbox']}`; diện tích usable `{part['usable_area_px']}`px²; neo OCR `{part['anchor']}`.",
            f"Font metrics ascent/descent `{target['font_metrics']}`. Height trong plan = {part['lines'][0]['height']}px; bước Y = `{target['line_top_steps_px']}`.", "",
            "| Dòng | Text | X | Y plan | Height plan | Height font bbox | Baseline Y thực | Gap ink tới dòng sau | Bước baseline tới dòng sau |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
        for line in line_records:
            markdown.append(f"| {line['line']} | {line['text']} | {line['x']} | {line['y']} | {line['planned_height_px']} | {line['actual_height_px']} | {line['baseline_y']} | {line['ink_gap_to_next_px']} | {line['baseline_step_to_next_px']} |")
        markdown.extend(["", "Gap ink âm nghĩa là bao glyph lấn vào bao dòng sau theo chiều dọc; không nhất thiết các nét chữ ở cùng cột X giao nhau. Height font bbox có thể chứa một hàng trống; gap được tính từ ink bbox raster thực.", "",
            f"Cặp giao pixel thực: `{target['pixel_overlap_pairs']}`. Cặp ink bbox giao nhau: `{target['bbox_overlap_pairs']}`.", "",
            f"![Saved render / plan boxes / ink boxes / glyph colors]({visual['image']})", ""])
    pixel_pairs = sum(len(part["pixel_overlap_pairs"]) for part in parts)
    bbox_pairs = sum(len(part["bbox_overlap_pairs"]) for part in parts)
    markdown.extend(["## Đánh giá plan", "",
        "Plan chọn cỡ font và vị trí đúng với bộ dàn hiện tại, nhưng height dùng để lập slots là ascent+descent của TTF thay vì bao glyph thực tế tiếng Việt. Vì thế cùng hệ số 1.6 không tạo khoảng trống nhìn thấy đồng đều.",
        "Renderer dùng anchor `lt`: mỗi dòng đặt đỉnh hộp glyph vào Y plan. Các dòng có dấu cao/thấp khác nhau sẽ có baseline khác nhau, dù bước Y plan đều. Cần phân biệt bước giữa các đỉnh hộp dòng với khoảng trống giữa các nét chữ.",
        f"Vùng được kiểm tra có {pixel_pairs} cặp giao pixel thực và {bbox_pairs} cặp ink bbox lấn theo chiều dọc. Điều kiện fitted/safe-mask chưa kiểm tra khoảng hở giữa dòng; bố cục vẫn có thể nhìn sát/dính dấu.",
        "Hướng sửa tổng quát: đo bao glyph ở font được chọn, lập dòng theo baseline chung với khoảng hở thực tế và kiểm tra giao/khoảng cách từng dòng. Cỡ font chung và các giới hạn OCR/viền 10px vẫn giữ. Lượt này chỉ kiểm tra saved plan, không đổi renderer hoặc source plan.", ""])
    artifact = {"source":str(source),"page":page_number,"utterance":utterance,"plan_sha256":file_hash(plan_path),
        "font_size_px":row["font_size_px"],"line_spacing":data["config"]["line_spacing"],"parts":parts}
    atomic_json(output / "inspection.json",artifact)
    (output / "README.vi.md").write_text("\n".join(markdown),encoding="utf-8")
    print(f"inspection: {output / 'README.vi.md'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True,type=Path)
    parser.add_argument("--page",required=True,type=int)
    parser.add_argument("--utterance",required=True,type=int)
    parser.add_argument("--output",required=True,type=Path)
    args = parser.parse_args()
    inspect(args.source,args.page,args.utterance,args.output)
